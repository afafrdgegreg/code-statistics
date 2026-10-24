# -*- coding: utf-8 -*-
"""扫描与统计核心引擎。

不依赖 tkinter，可单独 import 用于 CLI 或单元测试。
对外的关键接口：
    iter_stats(root, ...)  -> 生成器，逐文件产出 FileStat
    scan(root, ...)        -> 一次性返回 ScanResult
    aggregate(files)       -> 按类别聚合
    by_language(files)     -> 按语言聚合
    earliest(files, key)   -> 最早时间对应的文件
    build_tree(files)      -> 嵌套字典形式的目录树
"""

from __future__ import annotations

import codecs
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from file_classify import (
    CATEGORY_CONFIG,
    CATEGORY_DOC,
    CATEGORY_SKIP,
    CATEGORY_UNKNOWN,
    IGNORE_DIRS,
    classify,
    get_comment_style,
    looks_binary,
)

# --------------------------------------------------------------------------
# 编码探测：charset-normalizer 存在则用，否则降级到固定顺序
# --------------------------------------------------------------------------
try:  # pragma: no cover - 取决于运行环境
    from charset_normalizer import from_bytes as _cn_from_bytes
except ImportError:
    _cn_from_bytes = None

FALLBACK_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "big5", "cp1252", "latin-1")

# BOM 前缀 → 明确的编码
_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


@dataclass
class FileStat:
    """单个文件的统计结果。"""
    path: Path
    rel: str
    category: str
    language: str
    size: int = 0
    ctime: float = 0.0
    mtime: float = 0.0
    encoding: str = ""
    total_chars: int = 0
    nonspace_chars: int = 0
    total_lines: int = 0
    code_lines: int = 0
    blank_lines: int = 0
    comment_lines: int = 0

    @property
    def code_ext(self) -> str:
        return self.path.suffix.lower()

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class ScanResult:
    """一次完整扫描的结果。"""
    root: Path
    files: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)   # 原因 → 数量
    skipped_samples: dict = field(default_factory=dict)  # 原因 → [示例路径]
    errors: list = field(default_factory=list)    # (路径, 错误信息)
    total_seen: int = 0
    elapsed: float = 0.0
    cancelled: bool = False


# --------------------------------------------------------------------------
# 解码
# --------------------------------------------------------------------------
def decode_bytes(raw: bytes) -> tuple[str, str]:
    """把字节解码为文本，返回 (文本, 使用的编码名)。

    永远不抛异常：最后一档 latin-1 可以解码任意字节。
    """
    if not raw:
        return "", "utf-8"

    # 1. BOM 优先
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            try:
                return raw.decode(enc), enc
            except (UnicodeDecodeError, LookupError):
                break

    # 2. charset-normalizer 猜测
    if _cn_from_bytes is not None:
        try:
            best = _cn_from_bytes(raw).best()
            if best is not None:
                enc = best.encoding
                text = str(best)
                if text:
                    return text, enc
        except Exception:
            pass  # 猜测失败就走固定顺序

    # 3. 固定顺序尝试
    for enc in FALLBACK_ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue

    # 4. 兜底（理论上到不了这里）
    return raw.decode("latin-1", errors="replace"), "latin-1"


def read_text(path: str, size_limit: int | None = None) -> tuple[str, str, int]:
    """读取文件文本，返回 (文本, 编码, 字节数)。

    size_limit 不为 None 时，超过该大小的文件只读取前 size_limit 字节。
    """
    with open(path, "rb") as fh:
        raw = fh.read() if size_limit is None else fh.read(size_limit)
    text, enc = decode_bytes(raw)
    return text, enc, len(raw)


# --------------------------------------------------------------------------
# 行数/字数统计
# --------------------------------------------------------------------------
def count_lines(text: str, language: str) -> tuple[int, int, int, int, int]:
    """统计行数，返回 (总行, 代码行, 空行, 注释行, 非空白字符数)。

    注释判定用逐行状态机近似处理块注释。约定：
      - 空行：strip() 后为空
      - 注释行：该行去掉空白后以注释前缀开头，或处于块注释内且不含代码
      - 代码行：其余非空行（同一行既有代码又有注释的，计为代码行）
    """
    line_prefixes, block_pair = get_comment_style(language)

    lines = text.splitlines()
    total = len(lines)

    blank = 0
    comment = 0
    code = 0
    in_block = False
    block_start = block_end = ""

    if block_pair:
        # 允许 ((start, end), ...) 或单个 (start, end)
        if isinstance(block_pair[0], (tuple, list)):
            block_pairs = tuple(block_pair)
        else:
            block_pairs = (tuple(block_pair),)
    else:
        block_pairs = ()

    for line in lines:
        stripped = line.strip()

        if not stripped:
            blank += 1
            continue

        has_code = False   # 行内出现过真正的代码
        saw_comment = False  # 行内出现过注释
        in_comment_only = in_block  # 上一行进入块注释，本行是否从注释中开始

        # --- 扫描本行，把代码与注释切分出来 ---
        work = stripped
        while work:
            if in_block:
                # 先看单行注释前缀：处于块注释内时它们只是普通内容，不特殊处理
                idx = work.find(block_end)
                if idx == -1:
                    work = ""
                    break  # 整行都还在块注释里
                work = work[idx + len(block_end):].strip()
                in_block = False
                continue

            # 找最早出现的“块注释起始”与“行注释起始”
            block_hit = None
            for s, e in block_pairs:
                pos = work.find(s)
                if pos != -1 and (block_hit is None or pos < block_hit[0]):
                    block_hit = (pos, s, e)

            line_hit = None
            for p in line_prefixes:
                pos = work.find(p)
                if pos != -1 and (line_hit is None or pos < line_hit[0]):
                    line_hit = (pos, p)

            # 取两者中更靠前的；都没找到说明余下内容全是代码
            hit = None
            for cand in (block_hit, line_hit):
                if cand is not None and (hit is None or cand[0] < hit[0]):
                    hit = cand
            if hit is None:
                if work.strip():
                    has_code = True
                break

            pos, token = hit[0], hit[1]
            if work[:pos].strip():
                has_code = True  # 注释之前有代码
            saw_comment = True

            if block_hit is not None and pos == block_hit[0]:
                # 是块注释：找配对的结束标记
                _pos, s, e = block_hit
                rest = work[pos + len(s):]
                idx = rest.find(e)
                if idx == -1:
                    in_block = True
                    block_start, block_end = s, e
                    work = ""
                    break
                work = rest[idx + len(e):].strip()
            else:
                # 是行注释：本行剩余内容全部作废
                work = ""
                break

        # --- 归类 ---
        if has_code:
            code += 1          # 同一行既有代码又有注释，一律计为代码行
        elif saw_comment or in_comment_only:
            comment += 1
        else:
            code += 1

    nonspace = sum(1 for ch in text if not ch.isspace())
    return total, code, blank, comment, nonspace


def stat_one_file(
    path: str,
    root: str,
    category: str,
    language: str,
    size_limit: int | None = None,
    with_times: bool = True,
) -> FileStat:
    """统计单个文件，产出 FileStat。"""
    st = os.stat(path)
    text, enc, nbytes = read_text(path, size_limit)
    total, code, blank, comment, nonspace = count_lines(text, language)

    rel = os.path.relpath(path, root)
    return FileStat(
        path=Path(path),
        rel=rel,
        category=category,
        language=language,
        size=st.st_size,
        ctime=st.st_ctime if with_times else 0.0,
        mtime=st.st_mtime if with_times else 0.0,
        encoding=enc,
        total_chars=len(text),
        nonspace_chars=nonspace,
        total_lines=total,
        code_lines=code,
        blank_lines=blank,
        comment_lines=comment,
    )


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------
def iter_stats(
    root: str,
    ignore_dirs: bool = True,
    follow_links: bool = False,
    min_size: int = 0,
    max_size: int | None = None,
    size_limit: int | None = None,
    include_docs: bool = True,
    cancel_event: threading.Event | None = None,
    result: ScanResult | None = None,
):
    """遍历 root，逐个产出 FileStat。

    result 传入 ScanResult 时，跳过明细/错误/计数会写入其中。
    cancel_event 被 set 时停止扫描并返回已完成部分。
    """
    if result is None:
        result = ScanResult(root=Path(root))

    extra_ignore = {n.lower() for n in IGNORE_DIRS}

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_links):
        if cancel_event is not None and cancel_event.is_set():
            result.cancelled = True
            return

        # 忽略目录剪枝
        if ignore_dirs:
            kept = []
            for d in dirnames:
                if d.lower() in extra_ignore or d.endswith(".egg-info"):
                    _bump(result, "忽略的目录", os.path.join(dirpath, d))
                else:
                    kept.append(d)
            dirnames[:] = kept
        else:
            dirnames.sort()

        for filename in filenames:
            if cancel_event is not None and cancel_event.is_set():
                result.cancelled = True
                return

            full = os.path.join(dirpath, filename)
            result.total_seen += 1

            # 过滤规则全部集中在 classify() 里（它内部会先查已登记的完整文件名，
            # 再查扩展名黑名单），这里不再重复预筛，避免两处规则不一致。
            category, language = classify(filename)

            if category == CATEGORY_SKIP:
                _bump(result, "日志/二进制/媒体扩展名", full)
                continue

            if category == CATEGORY_UNKNOWN:
                # 扩展名没登记过：再做一次内容探测，是纯文本就按文档收进来，
                # 否则按二进制跳过。这样无扩展名的 README 不会被漏掉。
                if not _is_plain_text(full):
                    _bump(result, "二进制内容", full)
                    continue
                category, language = CATEGORY_DOC, "Text"

            if category == CATEGORY_DOC and not include_docs:
                _bump(result, "文档（用户排除）", full)
                continue

            if looks_binary(full):
                _bump(result, "二进制内容", full)
                continue

            try:
                size = os.path.getsize(full)
            except OSError as exc:
                result.errors.append((full, str(exc)))
                continue

            if size < min_size:
                _bump(result, "小于最小体积", full)
                continue
            if max_size is not None and size > max_size:
                _bump(result, "超过最大体积", full)
                continue

            try:
                fs = stat_one_file(
                    full, root, category, language, size_limit=size_limit
                )
            except OSError as exc:
                result.errors.append((full, str(exc)))
                continue
            except Exception as exc:  # 不让单个坏文件中断整次扫描
                result.errors.append((full, f"{type(exc).__name__}: {exc}"))
                continue

            yield fs


def _is_plain_text(path: str) -> bool:
    """对未登记扩展名的文件做保守判断：不含 NUL 且可被 utf-8/gbk 解码一小段。"""
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(4096)
    except OSError:
        return False
    if not chunk:
        return False
    if b"\x00" in chunk:
        return False
    for enc in ("utf-8", "gbk"):
        try:
            chunk.decode(enc)
            return True
        except UnicodeDecodeError:
            continue
    return False


def _bump(result: ScanResult, reason: str, path: str) -> None:
    result.skipped[reason] = result.skipped.get(reason, 0) + 1
    samples = result.skipped_samples.setdefault(reason, [])
    if len(samples) < 50:
        samples.append(path)


def scan(
    root: str,
    progress=None,
    cancel_event: threading.Event | None = None,
    **kwargs,
) -> ScanResult:
    """一次性完成扫描，返回 ScanResult。

    progress: 可选回调 (已处理文件数, 当前路径)，用于界面刷新进度。
    """
    t0 = time.time()
    result = ScanResult(root=Path(root))
    count = 0
    for fs in iter_stats(root, cancel_event=cancel_event, result=result, **kwargs):
        result.files.append(fs)
        count += 1
        if progress is not None and count % 25 == 0:
            progress(count, str(fs.path))
    if progress is not None:
        progress(count, "")
    result.elapsed = time.time() - t0
    return result


def count_files(root: str, ignore_dirs: bool = True, follow_links: bool = False) -> int:
    """快速预扫文件数，用于进度条百分比。仅数文件，不读内容。"""
    n = 0
    extra_ignore = {d.lower() for d in IGNORE_DIRS}
    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_links):
            if ignore_dirs:
                dirnames[:] = [
                    d for d in dirnames
                    if d.lower() not in extra_ignore and not d.endswith(".egg-info")
                ]
            n += len(filenames)
    except OSError:
        pass
    return n


# --------------------------------------------------------------------------
# 聚合
# --------------------------------------------------------------------------
_AGG_KEYS = (
    "files", "size", "total_chars", "nonspace_chars",
    "total_lines", "code_lines", "comment_lines", "blank_lines",
)


def _empty_agg() -> dict:
    return {k: 0 for k in _AGG_KEYS}


def _add(agg: dict, fs: FileStat) -> None:
    agg["files"] += 1
    agg["size"] += fs.size
    agg["total_chars"] += fs.total_chars
    agg["nonspace_chars"] += fs.nonspace_chars
    agg["total_lines"] += fs.total_lines
    agg["code_lines"] += fs.code_lines
    agg["comment_lines"] += fs.comment_lines
    agg["blank_lines"] += fs.blank_lines


def aggregate(files) -> dict:
    """按类别聚合，返回 {'code': {...}, 'config': {...}, 'doc': {...}, 'all': {...}}。"""
    out = {
        "code": _empty_agg(),
        "config": _empty_agg(),
        "doc": _empty_agg(),
        "all": _empty_agg(),
    }
    for fs in files:
        _add(out[fs.category] if fs.category in out else out["all"], fs)
        if fs.category != "all":
            _add(out["all"], fs)
    return out


def by_language(files) -> list[dict]:
    """按语言聚合，返回按字符数降序的列表。"""
    buckets: dict[tuple[str, str], dict] = {}
    for fs in files:
        key = (fs.category, fs.language or "未知")
        bucket = buckets.setdefault(key, {"category": key[0], "language": key[1], **_empty_agg()})
        _add(bucket, fs)
    return sorted(buckets.values(), key=lambda b: b["total_chars"], reverse=True)


def by_directory(files, root: str) -> list[dict]:
    """按一级子目录聚合。"""
    buckets: dict[str, dict] = {}
    root_abs = os.path.abspath(root)
    for fs in files:
        parent = os.path.dirname(os.path.abspath(str(fs.path)))
        rel_parent = os.path.relpath(parent, root_abs)
        top = "." if rel_parent == "." else rel_parent.split(os.sep)[0]
        bucket = buckets.setdefault(top, {"dir": top, **_empty_agg()})
        _add(bucket, fs)
    return sorted(buckets.values(), key=lambda b: b["total_chars"], reverse=True)


def earliest(files, key: str = "ctime") -> FileStat | None:
    """返回指定时间戳最早（非零）的文件。"""
    best = None
    best_val = None
    for fs in files:
        val = getattr(fs, key, 0.0)
        if not val:
            continue
        if best_val is None or val < best_val:
            best_val = val
            best = fs
    return best


def latest(files, key: str = "mtime") -> FileStat | None:
    """返回指定时间戳最新的文件。"""
    best = None
    best_val = None
    for fs in files:
        val = getattr(fs, key, 0.0)
        if not val:
            continue
        if best_val is None or val > best_val:
            best_val = val
            best = fs
    return best


# --------------------------------------------------------------------------
# 目录树
# --------------------------------------------------------------------------
def build_tree(files, root: str) -> dict:
    """把文件列表构造成嵌套字典。

    每个节点形如：
        {'_agg': {...}, '_files': [FileStat,...], '子目录名': {…节点…}}
    """
    root_abs = os.path.abspath(root)
    tree: dict = {"_agg": _empty_agg(), "_files": []}

    for fs in files:
        abs_path = os.path.abspath(str(fs.path))
        rel = os.path.relpath(abs_path, root_abs)
        parts = rel.split(os.sep)
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {"_agg": _empty_agg(), "_files": []})
        node["_files"].append(fs)

    _accumulate(tree)
    return tree


def _accumulate(node: dict) -> None:
    """自底向上累加子树的聚合值。"""
    for key, child in node.items():
        if key.startswith("_"):
            continue
        _accumulate(child)
        child_agg = child["_agg"]
        for k in _AGG_KEYS:
            node["_agg"][k] += child_agg[k]
    for fs in node["_files"]:
        _add(node["_agg"], fs)


def flatten_tree(tree: dict, prefix: str = "") -> list[tuple[str, dict]]:
    """把目录树压平成 [(显示路径, 节点)]，按路径排序，便于表格展示。"""
    out = []
    for key in sorted(k for k in tree if not k.startswith("_")):
        display = f"{prefix}{key}"
        out.append((display, tree[key]))
        out.extend(flatten_tree(tree[key], prefix=f"{display}{os.sep}"))
    return out


# --------------------------------------------------------------------------
# 格式化工具（GUI 与导出共用）
# --------------------------------------------------------------------------
def human_size(n: int) -> str:
    """字节数转人类可读。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} TB"


def human_number(n: int) -> str:
    """大数字加千分位。"""
    return f"{n:,}"


def human_time(ts: float, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """时间戳转字符串，0 显示为 '-'。"""
    if not ts:
        return "-"
    return time.strftime(fmt, time.localtime(ts))