# -*- coding: utf-8 -*-
"""代码统计工具 —— 图形界面入口。

运行：python code_stat_gui.py   （或直接双击本文件）
依赖：仅 Python 标准库；charset-normalizer 存在时会自动用于提升编码识别率。
"""

from __future__ import annotations

import csv
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from stats_core import (
    ScanResult,
    aggregate,
    build_tree,
    by_language,
    count_files,
    earliest,
    flatten_tree,
    human_number,
    human_size,
    human_time,
    iter_stats,
    latest,
)

APP_TITLE = "代码统计工具"
MAX_TABLE_ROWS = 2000  # 文件列表前台渲染上限，超过则提示导出

# Treeview 列的宽度配置
TREE_COLS = (
    ("name", "名称", 380, "w"),
    ("chars", "字符数", 110, "e"),
    ("lines", "总行", 90, "e"),
    ("code", "代码行", 90, "e"),
    ("lang", "语言/类型", 150, "w"),
)

FILE_COLS = (
    ("rel", "相对路径", 340, "w"),
    ("category", "类别", 70, "center"),
    ("language", "语言", 130, "w"),
    ("total_chars", "字符数", 90, "e"),
    ("nonspace_chars", "非空白", 90, "e"),
    ("total_lines", "总行", 70, "e"),
    ("code_lines", "代码行", 70, "e"),
    ("comment_lines", "注释行", 70, "e"),
    ("blank_lines", "空行", 60, "e"),
    ("size", "字节", 90, "e"),
    ("encoding", "编码", 90, "w"),
    ("ctime", "创建时间", 140, "w"),
    ("mtime", "修改时间", 140, "w"),
)

LANG_COLS = (
    ("language", "语言", 180, "w"),
    ("category", "类别", 80, "center"),
    ("files", "文件数", 80, "e"),
    ("total_chars", "字符数", 110, "e"),
    ("nonspace_chars", "非空白", 110, "e"),
    ("total_lines", "总行", 90, "e"),
    ("code_lines", "代码行", 90, "e"),
    ("comment_lines", "注释行", 90, "e"),
    ("blank_lines", "空行", 80, "e"),
)

SKIP_REASON_ORDER = (
    "忽略的目录",
    "日志/二进制/媒体扩展名",
    "二进制内容",
    "文档（用户排除）",
    "小于最小体积",
    "超过最大体积",
)

CATEGORY_LABEL = {"code": "代码", "config": "配置", "doc": "文档"}


class StatApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x760")
        self.root.minsize(940, 620)

        self.directory = tk.StringVar()
        self.ignore_dirs = tk.BooleanVar(value=True)
        self.follow_links = tk.BooleanVar(value=False)
        self.include_docs = tk.BooleanVar(value=True)
        self.min_size = tk.StringVar(value="0")
        self.status = tk.StringVar(value="请选择一个目录，然后点击「开始统计」。")

        self.result: ScanResult | None = None
        self.queue: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.total_estimate = 0
        self.sort_state: dict[str, tuple[str, bool]] = {}
        self._iid_seq = 0
        self._file_rows: list[tuple] = []

        self._build_ui()
        self._poll_queue()

    # ------------------------------------------------------------------
    # 界面构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self._build_top()
        self._build_summary()
        self._build_notebook()
        self._build_bottom()

    def _build_top(self) -> None:
        outer = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        outer.pack(fill="x")

        row = ttk.Frame(outer)
        row.pack(fill="x")
        ttk.Button(row, text="选择目录…", command=self.pick_directory, width=12).pack(side="left")
        self.dir_entry = ttk.Entry(row, textvariable=self.directory)
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.btn_start = ttk.Button(row, text="开始统计", command=self.start_scan, width=12)
        self.btn_start.pack(side="left")
        self.btn_cancel = ttk.Button(
            row, text="取消", command=self.cancel_scan, width=8, state="disabled"
        )
        self.btn_cancel.pack(side="left", padx=(6, 0))

        opts = ttk.Frame(outer)
        opts.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(
            opts, text="忽略常见目录（.git / node_modules / venv / build …）",
            variable=self.ignore_dirs,
        ).pack(side="left")
        ttk.Checkbutton(opts, text="跟随符号链接", variable=self.follow_links).pack(side="left", padx=16)
        ttk.Checkbutton(opts, text="统计文档（.md/.txt）", variable=self.include_docs).pack(side="left")
        ttk.Label(opts, text="最小体积(字节)：").pack(side="left", padx=(16, 2))
        ttk.Entry(opts, textvariable=self.min_size, width=8).pack(side="left")

        prog = ttk.Frame(outer)
        prog.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        ttk.Label(prog, textvariable=self.status, anchor="w").pack(fill="x", pady=(3, 0))

    def _build_summary(self) -> None:
        wrap = ttk.Frame(self.root, padding=(10, 6))
        wrap.pack(fill="x")

        self.code_card = self._make_card(wrap, "代码", 0)
        self.config_card = self._make_card(wrap, "配置", 1)
        self.doc_card = self._make_card(wrap, "文档", 2)

        times = ttk.LabelFrame(wrap, text="时间范围", padding=(8, 4))
        times.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        wrap.columnconfigure(0, weight=1)

        self.earliest_ctime = tk.StringVar(value="最早创建时间：-")
        self.earliest_mtime = tk.StringVar(value="最早修改时间：-")
        self.latest_mtime = tk.StringVar(value="最近修改时间：-")
        self.lang_summary = tk.StringVar(value="语言分布：-")

        ttk.Label(times, textvariable=self.earliest_ctime, anchor="w").pack(fill="x")
        ttk.Label(times, textvariable=self.earliest_mtime, anchor="w").pack(fill="x")
        ttk.Label(times, textvariable=self.latest_mtime, anchor="w").pack(fill="x")
        ttk.Label(times, textvariable=self.lang_summary, anchor="w").pack(fill="x", pady=(4, 0))

    def _make_card(self, parent, title: str, col: int) -> dict:
        frame = ttk.LabelFrame(parent, text=title, padding=(8, 4))
        frame.grid(row=0, column=col, sticky="nsew", padx=(0, 6) if col < 2 else 0)
        parent.columnconfigure(col, weight=1)

        vars_ = {}
        for i, (key, label) in enumerate(
            (("files", "文件数"), ("total_chars", "总字符"), ("nonspace_chars", "不含空白"),
             ("total_lines", "总行数"), ("code_lines", "代码行"), ("comment_lines", "注释行"),
             ("blank_lines", "空行"), ("size", "体积"))
        ):
            ttk.Label(frame, text=f"{label}：", anchor="w").grid(row=i, column=0, sticky="w")
            v = tk.StringVar(value="0")
            vars_[key] = v
            ttk.Label(frame, textvariable=v, anchor="e", font=("Consolas", 9)).grid(
                row=i, column=1, sticky="e", padx=(8, 0)
            )
        frame.columnconfigure(1, weight=1)
        return vars_

    def _build_notebook(self) -> None:
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.notebook = nb

        # --- 页1：目录树 ---
        page1 = ttk.Frame(nb)
        nb.add(page1, text="目录树")
        self.tree = self._make_table(page1, TREE_COLS, "tree")
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)

        bar1 = ttk.Frame(page1)
        bar1.pack(fill="x", pady=(4, 0))
        ttk.Button(bar1, text="全部展开", command=lambda: self._expand_all(True)).pack(side="left")
        ttk.Button(bar1, text="全部折叠", command=lambda: self._expand_all(False)).pack(side="left", padx=6)
        ttk.Button(bar1, text="导出本页 CSV", command=lambda: self.export_csv("tree")).pack(side="right")

        # --- 页2：文件列表 ---
        page2 = ttk.Frame(nb)
        nb.add(page2, text="文件列表")
        flt = ttk.Frame(page2)
        flt.pack(fill="x", pady=(0, 4))
        ttk.Label(flt, text="筛选：").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._apply_filter())
        ttk.Entry(flt, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=4)
        self.filter_hint = tk.StringVar(value="")
        ttk.Label(flt, textvariable=self.filter_hint).pack(side="left", padx=6)
        self.file_table = self._make_table(page2, FILE_COLS, "file")

        bar2 = ttk.Frame(page2)
        bar2.pack(fill="x", pady=(4, 0))
        self.list_hint = tk.StringVar(value="")
        ttk.Label(bar2, textvariable=self.list_hint).pack(side="left")
        ttk.Button(bar2, text="导出本页 CSV", command=lambda: self.export_csv("file")).pack(side="right")

        # --- 页3：按语言 ---
        page3 = ttk.Frame(nb)
        nb.add(page3, text="按语言")
        self.lang_table = self._make_table(page3, LANG_COLS, "lang")
        bar3 = ttk.Frame(page3)
        bar3.pack(fill="x", pady=(4, 0))
        ttk.Button(bar3, text="导出本页 CSV", command=lambda: self.export_csv("lang")).pack(side="right")

        # --- 页4：跳过明细 ---
        page4 = ttk.Frame(nb)
        nb.add(page4, text="跳过明细")
        ttk.Label(
            page4,
            text="以下内容未被统计。若发现本该统计的文件出现在这里，请检查过滤规则。",
            anchor="w",
        ).pack(fill="x", pady=(6, 4))
        cols = (("reason", "原因", 240, "w"), ("count", "数量", 90, "e"), ("sample", "示例", 700, "w"))
        self.skip_table = self._make_table(page4, cols, "skip", height=10)
        ttk.Label(page4, text="读取失败：", anchor="w").pack(fill="x", pady=(8, 2))
        ecols = (("path", "路径", 700, "w"), ("error", "错误", 400, "w"))
        self.err_table = self._make_table(page4, ecols, "err", height=6)

        # --- 页5：按目录 ---
        page5 = ttk.Frame(nb)
        nb.add(page5, text="按目录")
        dcols = (
            ("dir", "目录", 300, "w"),
            ("files", "文件数", 90, "e"),
            ("total_chars", "字符数", 120, "e"),
            ("total_lines", "总行", 100, "e"),
            ("code_lines", "代码行", 100, "e"),
            ("comment_lines", "注释行", 100, "e"),
            ("size", "体积", 100, "e"),
        )
        self.dir_table = self._make_table(page5, dcols, "dir")

    def _make_table(self, parent, cols, key: str, height: int = 18) -> ttk.Treeview:
        holder = ttk.Frame(parent)
        holder.pack(fill="both", expand=True)

        vbar = ttk.Scrollbar(holder, orient="vertical")
        vbar.pack(side="right", fill="y")
        hbar = ttk.Scrollbar(holder, orient="horizontal")
        hbar.pack(side="bottom", fill="x")

        names = [c[0] for c in cols]
        table = ttk.Treeview(
            holder, columns=names, show="headings", height=height,
            yscrollcommand=vbar.set, xscrollcommand=hbar.set,
        )
        vbar.config(command=table.yview)
        hbar.config(command=table.xview)
        table.pack(side="left", fill="both", expand=True)

        for name, label, width, anchor in cols:
            table.heading(name, text=label, command=lambda c=name, k=key: self.sort_table(k, c))
            table.column(name, width=width, anchor=anchor, stretch=(name in ("rel", "name", "sample", "path", "reason")))

        table.tag_configure("code", foreground="#1a4d80")
        table.tag_configure("config", foreground="#7a5c00")
        table.tag_configure("doc", foreground="#4a4a4a")
        return table

    def _build_bottom(self) -> None:
        bar = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text="导出完整 JSON", command=self.export_json).pack(side="left")
        ttk.Button(bar, text="导出 HTML 报告", command=self.export_html).pack(side="left", padx=6)
        ttk.Button(bar, text="导出全部 CSV", command=self.export_all_csv).pack(side="left")
        self.foot_status = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.foot_status, anchor="e").pack(side="right")

    # ------------------------------------------------------------------
    # 扫描流程
    # ------------------------------------------------------------------
    def pick_directory(self) -> None:
        chosen = filedialog.askdirectory(title="选择要统计的目录")
        if chosen:
            self.directory.set(os.path.normpath(chosen))

    def start_scan(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return

        root_dir = self.directory.get().strip()
        if not root_dir or not os.path.isdir(root_dir):
            messagebox.showwarning(APP_TITLE, "请先选择一个有效的目录。")
            return

        try:
            min_size = int(float(self.min_size.get() or 0))
        except ValueError:
            messagebox.showwarning(APP_TITLE, "最小体积必须是数字。")
            return

        self.result = None
        self.cancel_event.clear()
        self.progress.config(mode="determinate", value=0)
        self.status.set("正在预扫文件数量…")
        self.btn_start.config(state="disabled")
        self.btn_cancel.config(state="normal")
        self._clear_tables()

        self.worker = threading.Thread(
            target=self._scan_worker,
            args=(root_dir, min_size),
            daemon=True,
        )
        self.worker.start()

    def _scan_worker(self, root_dir: str, min_size: int) -> None:
        try:
            estimate = count_files(
                root_dir,
                ignore_dirs=self.ignore_dirs.get(),
                follow_links=self.follow_links.get(),
            )
            self.queue.put(("estimate", estimate))

            result = ScanResult(root=__import__("pathlib").Path(root_dir))
            done = 0
            for fs in iter_stats(
                root_dir,
                ignore_dirs=self.ignore_dirs.get(),
                follow_links=self.follow_links.get(),
                min_size=min_size,
                include_docs=self.include_docs.get(),
                cancel_event=self.cancel_event,
                result=result,
            ):
                result.files.append(fs)
                done += 1
                if done % 25 == 0:
                    self.queue.put(("progress", done, str(fs.path)))

            result.elapsed = 0.0
            self.queue.put(("done", result, done))
        except Exception as exc:  # 线程内异常必须回传，否则界面会静默卡住
            self.queue.put(("error", f"{type(exc).__name__}: {exc}"))

    def cancel_scan(self) -> None:
        self.cancel_event.set()
        self.status.set("正在取消…")
        self.btn_cancel.config(state="disabled")

    def _poll_queue(self) -> None:
        """主线程消费工作线程的消息，避免跨线程操作控件。"""
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]

                if kind == "estimate":
                    self.total_estimate = msg[1]
                    self.progress.config(maximum=max(self.total_estimate, 1))
                elif kind == "progress":
                    done, path = msg[1], msg[2]
                    self.progress.config(value=done)
                    pct = f"{done / max(self.total_estimate, 1) * 100:.1f}%"
                    self.status.set(f"已统计 {done} 个文件（{pct}）  当前：{self._shorten(path)}")
                elif kind == "done":
                    self._on_scan_done(msg[1], msg[2])
                elif kind == "error":
                    self._on_scan_error(msg[1])
        except queue.Empty:
            pass
        self.root.after(60, self._poll_queue)

    def _shorten(self, path: str, limit: int = 90) -> str:
        return path if len(path) <= limit else "…" + path[-(limit - 1):]

    def _on_scan_error(self, message: str) -> None:
        self.btn_start.config(state="normal")
        self.btn_cancel.config(state="disabled")
        self.status.set("扫描失败。")
        messagebox.showerror(APP_TITLE, f"扫描过程中出错：\n{message}")

    def _on_scan_done(self, result: ScanResult, done: int) -> None:
        self.result = result
        self.btn_start.config(state="normal")
        self.btn_cancel.config(state="disabled")

        self.progress.config(value=self.progress["maximum"])
        if result.cancelled:
            self.status.set(f"已取消。本次统计 {done} 个文件（结果不完整）。")
        else:
            self.status.set(f"完成：统计 {done} 个文件，耗时 {result.elapsed:.2f} 秒。")

        self._fill_summary(result)

        # 逐页填充。某一页出错不应让其余页面空白，因此单独捕获并汇总报告。
        page_errors = []
        for label, fn in (
            ("目录树", self._fill_tree),
            ("文件列表", self._fill_files),
            ("按语言", self._fill_languages),
            ("跳过明细", self._fill_skips),
            ("按目录", self._fill_directories),
        ):
            try:
                fn(result)
            except Exception as exc:
                page_errors.append(f"{label}：{type(exc).__name__}: {exc}")

        if page_errors:
            self.status.set(f"完成，但部分视图渲染失败（{len(page_errors)} 处）。")
            messagebox.showwarning(
                APP_TITLE, "以下视图渲染失败，数据本身已统计完成：\n\n" + "\n".join(page_errors)
            )

        self.foot_status.set(
            f"共检查 {result.total_seen} 项，统计 {len(result.files)} 个文件，"
            f"跳过 {sum(result.skipped.values())} 项，读取失败 {len(result.errors)} 个。"
        )

    # ------------------------------------------------------------------
    # 填充各页
    # ------------------------------------------------------------------
    def _clear_tables(self) -> None:
        for table in (self.tree, self.file_table, self.lang_table, self.skip_table,
                      self.err_table, self.dir_table):
            table.delete(*table.get_children())
        for var in (self.earliest_ctime, self.earliest_mtime, self.latest_mtime, self.lang_summary):
            var.set("")
        for card in (self.code_card, self.config_card, self.doc_card):
            for v in card.values():
                v.set("0")
        self.list_hint.set("")
        self.filter_hint.set("")

    def _fill_summary(self, result: ScanResult) -> None:
        agg = aggregate(result.files)

        for card, key in ((self.code_card, "code"), (self.config_card, "config"), (self.doc_card, "doc")):
            a = agg[key]
            card["files"].set(human_number(a["files"]))
            card["total_chars"].set(human_number(a["total_chars"]))
            card["nonspace_chars"].set(human_number(a["nonspace_chars"]))
            card["total_lines"].set(human_number(a["total_lines"]))
            card["code_lines"].set(human_number(a["code_lines"]))
            card["comment_lines"].set(human_number(a["comment_lines"]))
            card["blank_lines"].set(human_number(a["blank_lines"]))
            card["size"].set(human_size(a["size"]))

        e_c = earliest(result.files, "ctime")
        e_m = earliest(result.files, "mtime")
        l_m = latest(result.files, "mtime")
        self.earliest_ctime.set(
            f"最早创建时间：{human_time(e_c.ctime) if e_c else '-'}"
            + (f"   （{e_c.rel}）" if e_c else "")
        )
        self.earliest_mtime.set(
            f"最早修改时间：{human_time(e_m.mtime) if e_m else '-'}"
            + (f"   （{e_m.rel}）" if e_m else "")
        )
        self.latest_mtime.set(
            f"最近修改时间：{human_time(l_m.mtime) if l_m else '-'}"
            + (f"   （{l_m.rel}）" if l_m else "")
        )

        langs = by_language(result.files)
        top = "、".join(
            f"{b['language']} {human_number(b['total_chars'])}" for b in langs[:5]
        )
        self.lang_summary.set(f"语言分布（按字符数）：{top or '-'}")

    def _fill_tree(self, result: ScanResult) -> None:
        self.tree.delete(*self.tree.get_children())
        self.list_hint.set("")
        tree = build_tree(result.files, str(result.root))
        root_label = os.path.basename(str(result.root).rstrip("\\/")) or str(result.root)

        total_files = len(result.files)
        self.tree.insert(
            "", "end", iid="__root__", text=root_label, open=True,
            values=(root_label, human_number(tree["_agg"]["total_chars"]),
                    human_number(tree["_agg"]["total_lines"]), "", "（总计）"),
        )

        self._insert_tree_children("__root__", tree, depth=1)

        if not result.files:
            # 结果为空（如刚点取消）时给出明确提示，避免用户面对一片空白
            self.tree.insert(
                "__root__", "end", iid=self._new_iid(), text="（无统计结果）",
                values=("（无统计结果）", "0", "0", "0",
                        "已取消或该目录下没有可统计的文件"),
            )

        if total_files > MAX_TABLE_ROWS:
            self.list_hint.set("结果较多，目录树默认展开 2 层，可用「全部展开」查看完整结构。")

    def _insert_tree_children(self, parent_iid: str, node: dict, depth: int) -> None:
        """递归插入一层的子目录与文件。

        iid 由自增计数器生成，避免目录名与文件名构造出相同 iid 导致
        TclError: Item ... already exists。
        """
        for key in sorted(k for k in node if not k.startswith("_")):
            child = node[key]
            agg = child["_agg"]
            iid = self._new_iid()
            self.tree.insert(
                parent_iid, "end", iid=iid, text=key, open=(depth < 2),
                values=(key, human_number(agg["total_chars"]), human_number(agg["total_lines"]),
                        human_number(agg["code_lines"]), "目录"),
            )
            self._insert_tree_children(iid, child, depth + 1)

        # 本层直属文件
        for fs in sorted(node["_files"], key=lambda f: f.name.lower()):
            self.tree.insert(
                parent_iid, "end", iid=self._new_iid(), text=fs.name,
                values=(fs.name, human_number(fs.total_chars), human_number(fs.total_lines),
                        human_number(fs.code_lines), fs.language),
                tags=(fs.category,),
            )

    def _new_iid(self) -> str:
        self._iid_seq += 1
        return f"n{self._iid_seq}"

    def _fill_files(self, result: ScanResult) -> None:
        self._file_rows = []
        for fs in result.files:
            self._file_rows.append((
                fs.rel, CATEGORY_LABEL.get(fs.category, fs.category), fs.language,
                fs.total_chars, fs.nonspace_chars, fs.total_lines, fs.code_lines,
                fs.comment_lines, fs.blank_lines, fs.size, fs.encoding,
                human_time(fs.ctime), human_time(fs.mtime), fs.category,
            ))
        self._render_file_rows(self._file_rows)

    def _render_file_rows(self, rows) -> None:
        self.file_table.delete(*self.file_table.get_children())
        shown = rows[:MAX_TABLE_ROWS]
        for i, row in enumerate(shown):
            self.file_table.insert("", "end", iid=f"f{i}", values=row[:13], tags=(row[13],))
        self.filter_hint.set(f"显示 {len(shown)} / {len(rows)} 行")
        if len(rows) > MAX_TABLE_ROWS:
            self.list_hint.set(f"结果较多，仅显示前 {MAX_TABLE_ROWS} 行；完整数据请用底部「导出完整 JSON」或 CSV。")

    def _apply_filter(self) -> None:
        if not getattr(self, "_file_rows", None):
            return
        needle = self.filter_var.get().strip().lower()
        if not needle:
            self._render_file_rows(self._file_rows)
            return
        matched = [r for r in self._file_rows if needle in str(r[0]).lower()]
        self._render_file_rows(matched)

    def _fill_languages(self, result: ScanResult) -> None:
        self.lang_table.delete(*self.lang_table.get_children())
        for i, b in enumerate(by_language(result.files)):
            self.lang_table.insert(
                "", "end", iid=f"l{i}",
                values=(b["language"], CATEGORY_LABEL.get(b["category"], b["category"]),
                        human_number(b["files"]), human_number(b["total_chars"]),
                        human_number(b["nonspace_chars"]), human_number(b["total_lines"]),
                        human_number(b["code_lines"]), human_number(b["comment_lines"]),
                        human_number(b["blank_lines"])),
            )

    def _fill_directories(self, result: ScanResult) -> None:
        from stats_core import by_directory
        self.dir_table.delete(*self.dir_table.get_children())
        for i, b in enumerate(by_directory(result.files, str(result.root))):
            self.dir_table.insert(
                "", "end", iid=f"d{i}",
                values=(b["dir"], human_number(b["files"]), human_number(b["total_chars"]),
                        human_number(b["total_lines"]), human_number(b["code_lines"]),
                        human_number(b["comment_lines"]), human_size(b["size"])),
            )

    def _fill_skips(self, result: ScanResult) -> None:
        self.skip_table.delete(*self.skip_table.get_children())
        seen = set()
        for reason in SKIP_REASON_ORDER:
            if reason in result.skipped:
                seen.add(reason)
                self._insert_skip(reason, result)
        for reason in sorted(result.skipped):
            if reason not in seen:
                self._insert_skip(reason, result)

        self.err_table.delete(*self.err_table.get_children())
        for i, (path, err) in enumerate(result.errors[:500]):
            self.err_table.insert("", "end", iid=f"e{i}", values=(path, err))

    def _insert_skip(self, reason: str, result: ScanResult) -> None:
        samples = result.skipped_samples.get(reason, [])
        sample = samples[0] if samples else ""
        if len(samples) > 1:
            sample = f"{sample}  （等 {len(samples)} 个示例）"
        self.skip_table.insert(
            "", "end", values=(reason, human_number(result.skipped[reason]), sample)
        )

    # ------------------------------------------------------------------
    # 排序 / 展开
    # ------------------------------------------------------------------
    def sort_table(self, key: str, column: str) -> None:
        table = {"tree": self.tree, "file": self.file_table, "lang": self.lang_table,
                 "skip": self.skip_table, "err": self.err_table, "dir": self.dir_table}.get(key)
        if table is None or key in ("tree",):
            return  # 目录树按层级组织，不参与排序

        prev_col, prev_desc = self.sort_state.get(key, ("", False))
        desc = (not prev_desc) if prev_col == column else False
        self.sort_state[key] = (column, desc)

        col_index = {c[0]: i for i, c in enumerate(
            {"file": FILE_COLS, "lang": LANG_COLS, "dir": (
                ("dir", "目录", 300, "w"), ("files", "文件数", 90, "e"),
                ("total_chars", "字符数", 120, "e"), ("total_lines", "总行", 100, "e"),
                ("code_lines", "代码行", 100, "e"), ("comment_lines", "注释行", 100, "e"),
                ("size", "体积", 100, "e"),
            )}[key]
        )}[column]

        rows = []
        for iid in table.get_children(""):
            vals = table.item(iid, "values")
            rows.append((vals, iid))

        def keyfn(item):
            raw = str(item[0][col_index])
            cleaned = raw.replace(",", "").replace(" ", "")
            try:
                return (0, float(cleaned))
            except ValueError:
                return (1, raw.lower())

        rows.sort(key=keyfn, reverse=desc)
        for idx, (_, iid) in enumerate(rows):
            table.move(iid, "", idx)

        for name, label, _w, _a in {"file": FILE_COLS, "lang": LANG_COLS,
                                    "skip": (), "err": (), "dir": ()}.get(key, ()):
            if name == column:
                table.heading(name, text=f"{label} {'▼' if desc else '▲'}")
            else:
                table.heading(name, text=label)

    def _expand_all(self, expand: bool) -> None:
        def walk(iid: str) -> None:
            for child in self.tree.get_children(iid):
                self.tree.item(child, open=expand)
                walk(child)
        walk("__root__")

    def _on_tree_open(self, _event) -> None:
        pass  # 子树已在构建时一次性填入，展开无需再计算

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def _require_result(self) -> bool:
        if self.result is None or not self.result.files:
            messagebox.showinfo(APP_TITLE, "还没有可导出的统计结果，请先执行一次统计。")
            return False
        return True

    def export_csv(self, which: str) -> None:
        if not self._require_result():
            return
        default = f"{os.path.basename(str(self.result.root)) or 'result'}_{which}.csv"
        path = filedialog.asksaveasfilename(
            title="导出 CSV", defaultextension=".csv",
            initialfile=default, filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return

        table, cols = {
            "tree": (self.tree, TREE_COLS),
            "file": (self.file_table, FILE_COLS),
            "lang": (self.lang_table, LANG_COLS),
            "skip": (self.skip_table, (("reason", "原因", 0, "w"), ("count", "数量", 0, "e"),
                                       ("sample", "示例", 0, "w"))),
            "dir": (self.dir_table, (("dir", "目录", 0, "w"), ("files", "文件数", 0, "e"),
                                     ("total_chars", "字符数", 0, "e"), ("total_lines", "总行", 0, "e"),
                                     ("code_lines", "代码行", 0, "e"),
                                     ("comment_lines", "注释行", 0, "e"), ("size", "体积", 0, "e"))),
        }[which]

        try:
            # utf-8-sig 让 Excel 正确识别中文
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow([c[1] for c in cols])
                if which == "tree":
                    # Treeview 的行是嵌套的，必须递归遍历；
                    # get_children("") 只会返回根节点，其余会全部丢失。
                    self._write_tree_rows(w, depth_col=True)
                else:
                    for iid in table.get_children(""):
                        w.writerow(table.item(iid, "values"))
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"写入失败：{exc}")
            return
        self.foot_status.set(f"已导出：{path}")

    def _write_tree_rows(self, writer, parent: str = "", depth: int = 0, depth_col: bool = False) -> None:
        """递归写出目录树的每一行；depth 用缩进体现层级。"""
        for iid in self.tree.get_children(parent):
            vals = list(self.tree.item(iid, "values"))
            if depth_col:
                vals[0] = "    " * depth + str(vals[0])
            writer.writerow(vals)
            self._write_tree_rows(writer, iid, depth + 1, depth_col)

    def export_all_csv(self) -> None:
        if not self._require_result():
            return
        path = filedialog.askdirectory(title="选择导出目录")
        if not path:
            return
        from stats_core import by_directory
        written = []

        agg = aggregate(self.result.files)
        try:
            with open(os.path.join(path, "summary.csv"), "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["类别", "文件数", "总字符", "不含空白", "总行数", "代码行", "注释行", "空行", "字节"])
                for key, label in (("code", "代码"), ("config", "配置"), ("doc", "文档"), ("all", "合计")):
                    a = agg[key]
                    w.writerow([label, a["files"], a["total_chars"], a["nonspace_chars"],
                                a["total_lines"], a["code_lines"], a["comment_lines"],
                                a["blank_lines"], a["size"]])
            written.append("summary.csv")

            with open(os.path.join(path, "files.csv"), "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["相对路径", "类别", "语言", "总字符", "不含空白", "总行", "代码行",
                            "注释行", "空行", "字节", "编码", "创建时间", "修改时间"])
                for fs in self.result.files:
                    w.writerow([fs.rel, CATEGORY_LABEL.get(fs.category, fs.category), fs.language,
                                fs.total_chars, fs.nonspace_chars, fs.total_lines, fs.code_lines,
                                fs.comment_lines, fs.blank_lines, fs.size, fs.encoding,
                                human_time(fs.ctime), human_time(fs.mtime)])
            written.append("files.csv")

            with open(os.path.join(path, "languages.csv"), "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["语言", "类别", "文件数", "总字符", "不含空白", "总行", "代码行", "注释行", "空行"])
                for b in by_language(self.result.files):
                    w.writerow([b["language"], CATEGORY_LABEL.get(b["category"], b["category"]),
                                b["files"], b["total_chars"], b["nonspace_chars"], b["total_lines"],
                                b["code_lines"], b["comment_lines"], b["blank_lines"]])
            written.append("languages.csv")

            with open(os.path.join(path, "directories.csv"), "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["目录", "文件数", "总字符", "总行", "代码行", "注释行", "字节"])
                for b in by_directory(self.result.files, str(self.result.root)):
                    w.writerow([b["dir"], b["files"], b["total_chars"], b["total_lines"],
                                b["code_lines"], b["comment_lines"], b["size"]])
            written.append("directories.csv")

            with open(os.path.join(path, "skipped.csv"), "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["跳过原因", "数量"])
                for reason, n in self.result.skipped.items():
                    w.writerow([reason, n])
            written.append("skipped.csv")
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"写入失败：{exc}")
            return

        self.foot_status.set(f"已导出 {len(written)} 个 CSV 到：{path}")
        messagebox.showinfo(APP_TITLE, "已导出：\n" + "\n".join(written) + f"\n\n目录：{path}")

    def export_json(self) -> None:
        if not self._require_result():
            return
        default = f"{os.path.basename(str(self.result.root)) or 'result'}_stats.json"
        path = filedialog.asksaveasfilename(
            title="导出 JSON", defaultextension=".json",
            initialfile=default, filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return

        r = self.result
        e_c, e_m, l_m = earliest(r.files, "ctime"), earliest(r.files, "mtime"), latest(r.files, "mtime")
        payload = {
            "root": str(r.root),
            "generated_at": human_time(time.time()),
            "elapsed_seconds": round(r.elapsed, 3),
            "cancelled": r.cancelled,
            "summary": aggregate(r.files),
            "earliest_created": {"time": human_time(e_c.ctime), "file": e_c.rel} if e_c else None,
            "earliest_modified": {"time": human_time(e_m.mtime), "file": e_m.rel} if e_m else None,
            "latest_modified": {"time": human_time(l_m.mtime), "file": l_m.rel} if l_m else None,
            "by_language": by_language(r.files),
            "skipped": r.skipped,
            "errors": [{"path": p, "error": e} for p, e in r.errors],
            "files": [
                {
                    "path": fs.rel, "category": fs.category, "language": fs.language,
                    "size": fs.size, "encoding": fs.encoding,
                    "created": human_time(fs.ctime), "modified": human_time(fs.mtime),
                    "total_chars": fs.total_chars, "nonspace_chars": fs.nonspace_chars,
                    "total_lines": fs.total_lines, "code_lines": fs.code_lines,
                    "comment_lines": fs.comment_lines, "blank_lines": fs.blank_lines,
                }
                for fs in r.files
            ],
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"写入失败：{exc}")
            return
        self.foot_status.set(f"已导出：{path}")

    def export_html(self) -> None:
        if not self._require_result():
            return
        default = f"{os.path.basename(str(self.result.root)) or 'result'}_report.html"
        path = filedialog.asksaveasfilename(
            title="导出 HTML 报告", defaultextension=".html",
            initialfile=default, filetypes=[("HTML 文件", "*.html"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._render_html())
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"写入失败：{exc}")
            return
        self.foot_status.set(f"已导出：{path}")

    def _render_html(self) -> str:
        import html as html_mod

        r = self.result
        agg = aggregate(r.files)
        langs = by_language(r.files)
        e_c, e_m = earliest(r.files, "ctime"), earliest(r.files, "mtime")

        def esc(x) -> str:
            return html_mod.escape(str(x))

        rows = "".join(
            f"<tr><td>{esc(CATEGORY_LABEL.get(k, k))}</td><td class='n'>{a['files']:,}</td>"
            f"<td class='n'>{a['total_chars']:,}</td><td class='n'>{a['nonspace_chars']:,}</td>"
            f"<td class='n'>{a['total_lines']:,}</td><td class='n'>{a['code_lines']:,}</td>"
            f"<td class='n'>{a['comment_lines']:,}</td><td class='n'>{a['blank_lines']:,}</td>"
            f"<td class='n'>{human_size(a['size'])}</td></tr>"
            for k, a in (("code", agg["code"]), ("config", agg["config"]),
                         ("doc", agg["doc"]), ("all", agg["all"]))
        )

        max_chars = max((b["total_chars"] for b in langs), default=1)
        lang_rows = "".join(
            f"<tr><td>{esc(b['language'])}</td><td>{esc(CATEGORY_LABEL.get(b['category'], b['category']))}</td>"
            f"<td class='n'>{b['files']:,}</td><td class='n'>{b['total_chars']:,}</td>"
            f"<td class='n'>{b['total_lines']:,}</td><td class='n'>{b['code_lines']:,}</td>"
            f"<td class='n'>{b['comment_lines']:,}</td>"
            f"<td class='bar'><span style='width:{b['total_chars'] / max_chars * 100:.2f}%'></span></td></tr>"
            for b in langs[:40]
        )

        skip_rows = "".join(
            f"<tr><td>{esc(reason)}</td><td class='n'>{n:,}</td></tr>"
            for reason, n in sorted(r.skipped.items(), key=lambda kv: -kv[1])
        )

        return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>代码统计报告 - {esc(os.path.basename(str(r.root)))}</title>
<style>
 body{{font-family:"Microsoft YaHei",-apple-system,Segoe UI,sans-serif;margin:0;padding:28px 34px;
      background:#f5f6f8;color:#24292f;line-height:1.55}}
 h1{{font-size:21px;margin:0 0 4px}} h2{{font-size:15px;margin:26px 0 8px;color:#3a3f46}}
 .sub{{color:#6a737d;font-size:13px;margin-bottom:18px}}
 table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px;
        box-shadow:0 1px 3px rgba(0,0,0,.07);border-radius:6px;overflow:hidden}}
 th,td{{padding:7px 11px;border-bottom:1px solid #e8eaed;text-align:left}}
 th{{background:#f0f2f5;font-weight:600;color:#40464d}}
 td.n{{text-align:right;font-variant-numeric:tabular-nums;font-family:Consolas,monospace}}
 tr:last-child td{{border-bottom:none}}
 tr.total td{{font-weight:700;background:#fafbfc}}
 .bar{{width:150px;padding:0 11px}} .bar span{{display:block;height:9px;background:#1a4d80;border-radius:2px}}
 .cards{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:6px}}
 .card{{background:#fff;border-radius:6px;padding:13px 18px;box-shadow:0 1px 3px rgba(0,0,0,.07);min-width:150px}}
 .card .k{{font-size:12px;color:#6a737d}} .card .v{{font-size:23px;font-weight:600;margin-top:3px}}
</style></head><body>
<h1>代码统计报告</h1>
<div class="sub">目录：{esc(r.root)}　|　生成时间：{esc(human_time(time.time()))}　|
耗时 {r.elapsed:.2f} 秒　|　统计 {len(r.files):,} 个文件</div>
<div class="cards">
 <div class="card"><div class="k">代码字符数</div><div class="v">{agg['code']['total_chars']:,}</div></div>
 <div class="card"><div class="k">代码行数（非空非注释）</div><div class="v">{agg['code']['code_lines']:,}</div></div>
 <div class="card"><div class="k">配置字符数</div><div class="v">{agg['config']['total_chars']:,}</div></div>
 <div class="card"><div class="k">最早创建时间</div>
   <div class="v" style="font-size:15px">{esc(human_time(e_c.ctime) if e_c else '-')}</div></div>
</div>

<h2>分类汇总</h2>
<table><tr><th>类别</th><th>文件数</th><th>总字符</th><th>不含空白</th><th>总行数</th>
<th>代码行</th><th>注释行</th><th>空行</th><th>体积</th></tr>{rows}</table>

<h2>按语言分布（前 40）</h2>
<table><tr><th>语言</th><th>类别</th><th>文件数</th><th>字符数</th><th>总行</th>
<th>代码行</th><th>注释行</th><th>占比</th></tr>{lang_rows}</table>

<h2>跳过明细</h2>
<table><tr><th>原因</th><th>数量</th></tr>{skip_rows}</table>

<div class="sub" style="margin-top:22px">
最早创建：{esc(human_time(e_c.ctime) if e_c else '-')}（{esc(e_c.rel if e_c else '-')}）　|
最早修改：{esc(human_time(e_m.mtime) if e_m else '-')}（{esc(e_m.rel if e_m else '-')}）
</div>
</body></html>"""


def main() -> None:
    # 让本文件所在目录同时进入 sys.path，双击运行时才能 import stats_core
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except tk.TclError:
        pass
    StatApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()