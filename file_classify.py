# -*- coding: utf-8 -*-
"""文件分类与过滤规则。

本模块是纯数据表 + 纯函数，不依赖 tkinter，可单独 import。
新增语言或配置类型时，只需修改本文件顶部的几张表。
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------
# 1. 忽略目录黑名单（按目录名匹配，默认启用，界面可关闭）
# --------------------------------------------------------------------------
IGNORE_DIRS = frozenset({
    # 版本控制
    ".git", ".svn", ".hg", ".bzr",
    # 依赖与虚拟环境
    "node_modules", "venv", ".venv", "env", ".env", "virtualenv",
    "site-packages", "dist-packages", "vendor", "bower_components",
    # Python 缓存
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".eggs", "*.egg-info",
    # 构建产物
    "dist", "build", "target", "out", "bin", "obj", "release", "debug",
    ".gradle", ".next", ".nuxt", ".svelte-kit", "coverage", ".nyc_output",
    # IDE / 编辑器
    ".idea", ".vscode", ".vs", ".settings", ".metadata",
    # 其他
    ".cache", ".tmp", ".temp", "tmp", "logs", "log", "backup",
})

# --------------------------------------------------------------------------
# 2. 忽略扩展名黑名单：日志 / 二进制 / 媒体 / 归档 / 数据库等
# --------------------------------------------------------------------------
IGNORE_EXT = frozenset({
    # --- 日志 ---
    ".log", ".log1", ".log2", ".log3", ".out", ".err", ".trace",
    ".pid", ".dmp", ".stackdump",
    # --- 二进制 / 可执行 ---
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".obj", ".a", ".lib",
    ".class", ".jar", ".war", ".ear", ".pyc", ".pyo", ".pyd", ".pdb",
    ".elf", ".wasm", ".ko", ".deb", ".rpm", ".msi", ".apk", ".ipa",
    # --- 图像 / 媒体 ---
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff",
    ".tif", ".psd", ".ai", ".eps", ".raw", ".cr2", ".nef", ".heic",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".mkv", ".webm",
    ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".mid", ".midi",
    # --- 归档 / 压缩 ---
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz",
    ".zst", ".lz", ".lzma", ".cab", ".iso", ".whl", ".egg",
    # --- 数据库 / 二进制数据 ---
    ".db", ".sqlite", ".sqlite3", ".mdb", ".accdb", ".dat", ".idx",
    ".pack", ".nib", ".ldb", ".sst", ".orc", ".parquet", ".avro",
    # --- 字体 ---
    ".ttf", ".otf", ".woff", ".woff2", ".eot", ".fnt",
    # --- 锁文件 / 由机器生成 ---
    # 注意 Cargo.lock / poetry.lock 等依赖清单文件在 CONFIG_FILENAMES 里，
    # classify() 会先匹配完整文件名，因此不会被这里的 .lock 拦掉；
    # 这里拦的是 package-lock.json/yarn.lock 之外那些无意义的散落 .lock 文件。
    ".lock", ".map", ".min.js", ".min.css", ".beautify",
    # --- 其他 ---
    ".swp", ".swo", ".swn", ".bak", ".orig", ".rej", ".pyc.tmp",
    ".ds_store", ".lnk", ".url", ".crash", ".dSYM",
})

# 注意：.svg 是文本 XML，刻意不列入黑名单，按配置/文本统计。

# --------------------------------------------------------------------------
# 3. 代码扩展名 → 语言名
# --------------------------------------------------------------------------
CODE_EXT = {
    # 脚本语言
    ".py": "Python", ".pyw": "Python", ".pyi": "Python",
    ".rb": "Ruby", ".rake": "Ruby",
    ".php": "PHP", ".phtml": "PHP",
    ".pl": "Perl", ".pm": "Perl",
    ".lua": "Lua",
    ".r": "R", ".rmd": "R",
    ".jl": "Julia",
    ".tcl": "Tcl",
    ".groovy": "Groovy",
    ".dart": "Dart",
    ".zig": "Zig",
    ".nim": "Nim",
    ".v": "V",
    ".ex": "Elixir", ".exs": "Elixir",
    ".erl": "Erlang", ".hrl": "Erlang",
    ".hs": "Haskell",
    ".ml": "OCaml", ".mli": "OCaml",
    ".fs": "F#", ".fsx": "F#",
    ".clj": "Clojure", ".cljs": "Clojure", ".cljc": "Clojure",
    ".scala": "Scala", ".sc": "Scala",
    ".kt": "Kotlin", ".kts": "Kotlin",
    ".swift": "Swift",
    ".m": "Objective-C/Matlab", ".mm": "Objective-C++",
    ".cs": "C#", ".vb": "Visual Basic",
    ".java": "Java",
    ".pas": "Pascal", ".dpr": "Pascal",
    ".f": "Fortran", ".f90": "Fortran", ".f95": "Fortran", ".for": "Fortran",
    ".cob": "COBOL", ".cbl": "COBOL",
    ".adb": "Ada", ".ads": "Ada",
    ".d": "D",
    ".vhd": "VHDL", ".vhdl": "VHDL",
    ".elm": "Elm",
    # 系统语言
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".c++": "C++",
    ".hpp": "C++", ".hh": "C++", ".hxx": "C++",
    ".m4": "M4",
    ".asm": "Assembly", ".s": "Assembly", ".nasm": "Assembly",
    # 前端
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "React JSX",
    ".ts": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".tsx": "React TSX",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".html": "HTML", ".htm": "HTML", ".xhtml": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".sass": "Sass", ".less": "Less",
    ".styl": "Stylus",
    # 查询与数据
    ".sql": "SQL", ".psql": "SQL", ".plsql": "PL/SQL",
    ".graphql": "GraphQL", ".gql": "GraphQL",
    ".proto": "Protocol Buffers",
    # 命令行与脚本
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".ksh": "Shell",
    ".fish": "Shell", ".csh": "Shell",
    ".ps1": "PowerShell", ".psm1": "PowerShell", ".psd1": "PowerShell",
    ".bat": "Batch", ".cmd": "Batch",
    ".awk": "Awk", ".sed": "Sed",
    # 其他文本型源码
    ".sol": "Solidity",
    ".cu": "CUDA", ".cuh": "CUDA",
    ".rkt": "Racket",
    ".scm": "Scheme", ".ss": "Scheme",
    ".lisp": "Lisp", ".el": "Emacs Lisp",
    ".pro": "Prolog",
    ".tex": "TeX", ".cls": "TeX", ".sty": "TeX",
    ".ipynb": "Jupyter Notebook",
    ".gradle": "Gradle",
    ".cmake": "CMake",
}

# --------------------------------------------------------------------------
# 4. 配置扩展名 → 配置类型
# --------------------------------------------------------------------------
CONFIG_EXT = {
    ".json": "JSON", ".jsonc": "JSON", ".json5": "JSON5", ".geojson": "JSON",
    ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML",
    ".ini": "INI", ".cfg": "INI", ".conf": "INI", ".config": "INI",
    ".properties": "Properties",
    ".env": "Dotenv",
    ".xml": "XML", ".xsd": "XML", ".xsl": "XML", ".xslt": "XML",
    ".plist": "Plist",
    ".svg": "SVG",
    ".rc": "Windows RC",
    ".inf": "INI",
    ".reg": "Registry",
    ".editorconfig": "EditorConfig",
    ".htaccess": "Apache Config",
    ".service": "systemd Unit",
    ".desktop": "Desktop Entry",
    ".nsi": "NSIS Script",
    ".wxs": "WiX Config",
    ".rst": "reStructuredText",   # 归配置类文本
    ".dot": "Graphviz",
    ".pem": "Certificate", ".crt": "Certificate", ".cer": "Certificate",
    ".key": "Key File", ".pub": "Key File",
    ".csproj": "MSBuild", ".vbproj": "MSBuild", ".vcxproj": "MSBuild",
    ".sln": "Visual Studio Solution",
    ".pom": "Maven POM",
}

# --------------------------------------------------------------------------
# 5. 无扩展名 / 特殊文件名的配置与构建文件（按完整文件名匹配，小写比较）
# --------------------------------------------------------------------------
CONFIG_FILENAMES = frozenset({
    # 构建
    "makefile", "gnumakefile", "dockerfile", "containerfile",
    "cmakelists.txt", "build", "workspace", "build.bazel", "bazelrc",
    "sconstruct", "scsub", "meson.build", "jamfile", "rakefile",
    "gruntfile", "gulpfile", "webpack.config", "rollup.config",
    "vite.config", "babel.config", "jest.config", "tsconfig.json",
    "vagrantfile", "procfile", "brewfile", "justfile", "taskfile",
    # 依赖清单
    "requirements.txt", "requirements-dev.txt", "pipfile", "pipfile.lock",
    "poetry.lock", "setup.py", "setup.cfg", "pyproject.toml",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "gemfile", "gemfile.lock", "composer.json", "cargo.toml", "cargo.lock",
    "go.mod", "go.sum", "pom.xml", "build.gradle", "settings.gradle",
    # 忽略与属性文件
    ".gitignore", ".gitattributes", ".gitmodules", ".dockerignore",
    ".npmignore", ".eslintignore", ".prettierignore", ".helmignore",
    ".editorconfig", ".gitkeep",
    # 配置
    ".bashrc", ".bash_profile", ".zshrc", ".profile", ".vimrc",
    ".tmux.conf", ".inputrc", ".npmrc", ".yarnrc", ".babelrc",
    ".eslintrc", ".prettierrc", ".stylelintrc", ".flake8", ".pylintrc",
    ".gitconfig", ".curlrc", ".wgetrc", ".screenrc",
    "hosts", "nginx.conf", "httpd.conf", "my.cnf", "redis.conf",
    "manifest.json", "appsettings.json", "web.config", "app.config",
})

# --------------------------------------------------------------------------
# 6. 文档类扩展名（按用户决定：默认并入代码页展示，但保留独立类别）
# --------------------------------------------------------------------------
DOC_EXT = {
    ".md": "Markdown", ".markdown": "Markdown", ".mdown": "Markdown",
    ".txt": "Text", ".text": "Text", ".log.txt": "Text",
    ".rst.txt": "reStructuredText",
    ".adoc": "AsciiDoc", ".asciidoc": "AsciiDoc",
    ".org": "Org Mode", ".wiki": "Wiki",
    ".pdf": "PDF", ".doc": "Word", ".docx": "Word",
    ".rtf": "RTF", ".odt": "ODT", ".epub": "EPUB",
}

# --------------------------------------------------------------------------
# 语言 → 注释风格。键为 CODE_EXT / CONFIG_EXT / DOC_EXT 中的语言名。
# 值为 (行注释前缀元组, 块注释对元组)。
# --------------------------------------------------------------------------
COMMENT_STYLES = {
    # 井号系
    "Python": (("#",), (('"""', '"""'), ("'''", "'''"))),
    "Ruby": (("#",), ()),
    "Shell": (("#",), ()),
    "PowerShell": (("#",), ("<#", "#>")),
    "Perl": (("#",), ()),
    "R": (("#",), ()),
    "YAML": (("#",), ()),
    "TOML": (("#",), ()),
    "INI": (("#", ";"), ()),
    "Properties": (("#", "!"), ()),
    "Dotenv": (("#",), ()),
    "EditorConfig": (("#", ";"), ()),
    "Makefile": (("#",), ()),
    "Dockerfile": (("#",), ()),
    "CMake": (("#",), ()),
    "Gradle": (("//",), ()),
    "Apache Config": (("#",), ()),
    "systemd Unit": (("#", ";"), ()),
    "Desktop Entry": (("#",), ()),
    "Julia": (("#",), ("#=", "=#")),
    "Nim": (("#",), ()),
    "Elixir": (("#",), ()),
    "Tcl": (("#",), ()),
    "Awk": (("#",), ()),
    "Graphviz": (("//", "#"), ("/*", "*/")),
    "Key File": (("#",), ()),
    # C 系
    "C": (("//",), ("/*", "*/")),
    "C++": (("//",), ("/*", "*/")),
    "C#": (("//",), ("/*", "*/")),
    "Java": (("//",), ("/*", "*/")),
    "JavaScript": (("//",), ("/*", "*/")),
    "TypeScript": (("//",), ("/*", "*/")),
    "React JSX": (("//",), ("/*", "*/")),
    "React TSX": (("//",), ("/*", "*/")),
    "Go": (("//",), ("/*", "*/")),
    "Rust": (("//",), ("/*", "*/")),
    "PHP": (("//", "#"), ("/*", "*/")),
    "Swift": (("//",), ("/*", "*/")),
    "Kotlin": (("//",), ("/*", "*/")),
    "Scala": (("//",), ("/*", "*/")),
    "Dart": (("//",), ("/*", "*/")),
    "Zig": (("//",), ()),
    "D": (("//",), ("/*", "*/")),
    "V": (("//",), ("/*", "*/")),
    "Groovy": (("//",), ("/*", "*/")),
    "Objective-C/Matlab": (("//", "%"), ("/*", "*/")),
    "Objective-C++": (("//",), ("/*", "*/")),
    "Solidity": (("//",), ("/*", "*/")),
    "CUDA": (("//",), ("/*", "*/")),
    "Protocol Buffers": (("//",), ("/*", "*/")),
    "Haskell": (("--",), ("{-", "-}")),
    "Ada": (("--",), ()),
    "Elm": (("--",), ("{-", "-}")),
    "Visual Basic": (("'",), ()),
    "OCaml": ((), ("(*", "*)")),
    "F#": (("//",), ("(*", "*)")),
    "Pascal": (("//",), ("{", "}")),
    "CSS": ((), ("/*", "*/")),
    "SCSS": (("//",), ("/*", "*/")),
    "Less": (("//",), ("/*", "*/")),
    "Stylus": (("//",), ("/*", "*/")),
    # 破折号系
    "SQL": (("--",), ("/*", "*/")),
    "PL/SQL": (("--",), ("/*", "*/")),
    "Lua": (("--",), ("--[[", "]]")),
    "VHDL": (("--",), ()),
    # 分号系
    "Assembly": ((";", "#"), ()),
    "Lisp": ((";",), ("#|", "|#")),
    "Scheme": ((";",), ("#|", "|#")),
    "Racket": ((";",), ("#|", "|#")),
    "Clojure": ((";",), ()),
    "Emacs Lisp": ((";",), ()),
    "COBOL": (("*>",), ()),
    "NSIS Script": ((";", "#"), ()),
    "Registry": ((";",), ()),
    "Windows RC": (("//",), ("/*", "*/")),
    # 标记系
    "HTML": ((), ("<!--", "-->")),
    "XML": ((), ("<!--", "-->")),
    "Vue": (("//",), ("/*", "*/")),
    "Svelte": (("//",), ("/*", "*/")),
    "Markdown": ((), ("<!--", "-->")),
    "SVG": ((), ("<!--", "-->")),
    "XSL": ((), ("<!--", "-->")),
    "XSD": ((), ("<!--", "-->")),
    "Plist": ((), ("<!--", "-->")),
    # 其他
    "JSON": ((), ()),
    "JSON5": (("//",), ("/*", "*/")),
    "Text": ((), ()),
    "TeX": (("%",), ()),
    "Assembly": ((";", "#"), ()),
    "Fortran": (("!", "C", "*"), ()),
    "M4": (("dnl", "#"), ()),
    "Prolog": (("%",), ("/*", "*/")),
    "Erlang": (("%",), ()),
    "Matlab": (("%",), ("%{", "%}")),
    "GraphQL": (("#",), ()),
    "Jupyter Notebook": (("#",), ()),
    "MSBuild": ((), ("<!--", "-->")),
    "Visual Studio Solution": (("#",), ()),
    "Maven POM": ((), ("<!--", "-->")),
    "WiX Config": ((), ("<!--", "-->")),
    "Certificate": ((), ()),
    "Pem": ((), ()),
    "reStructuredText": ((), ()),
    "AsciiDoc": (("//",), ()),
    "Org Mode": (("#",), ()),
    "Wiki": ((), ()),
    "Batch": (("REM", "rem", "::"), ()),
    "Sed": (("#",), ()),
    "None": ((), ()),
}

DEFAULT_COMMENT_STYLE = ((), ())


def get_comment_style(language: str):
    """返回 (行注释前缀元组, 块注释对)；未登记的语言按无注释处理。"""
    return COMMENT_STYLES.get(language, DEFAULT_COMMENT_STYLE)


# --------------------------------------------------------------------------
# 分类主函数
# --------------------------------------------------------------------------
CATEGORY_CODE = "code"
CATEGORY_CONFIG = "config"
CATEGORY_DOC = "doc"
CATEGORY_SKIP = "skip"        # 明确排除（日志/二进制/媒体等）
CATEGORY_UNKNOWN = "unknown"  # 扩展名未登记，需调用方用内容探测决定


def classify(filename: str):
    """按文件名（不含目录）判定类别。

    返回 (category, language)，category ∈ {code, config, doc, skip, unknown}。
    判定顺序：配置文件名 → 忽略扩展名 → 配置扩展名 → 代码扩展名
              → 文档扩展名 → unknown

    已登记在 CONFIG_FILENAMES 里的完整文件名（含 Cargo.lock 等）优先于
    扩展名黑名单，确保依赖清单类的 .lock 文件不会被误排。

    unknown 与 skip 的区别：skip 表示规则明确要求排除（如 .log、.png），
    调用方应当直接丢弃；unknown 表示扩展名没登记过，调用方可以再做一次
    内容探测，把实际上是纯文本的文件（如无扩展名的 README）纳入统计。
    """
    name = filename
    lower = name.lower()
    ext = os.path.splitext(lower)[1]

    # 1. 特殊完整文件名优先（Makefile、Dockerfile、.gitignore、Cargo.lock 等）
    if lower in CONFIG_FILENAMES:
        return CATEGORY_CONFIG, guess_config_lang(lower)

    # 2. 扩展名黑名单（日志、二进制等）—— 明确排除，不再做内容探测
    if ext in IGNORE_EXT:
        return CATEGORY_SKIP, ""

    # 3. 配置扩展名
    if ext in CONFIG_EXT:
        return CATEGORY_CONFIG, CONFIG_EXT[ext]

    # 4. 代码扩展名
    if ext in CODE_EXT:
        return CATEGORY_CODE, CODE_EXT[ext]

    # 5. 文档扩展名
    if ext in DOC_EXT:
        return CATEGORY_DOC, DOC_EXT[ext]

    # 6. 未登记，交给调用方决定
    return CATEGORY_UNKNOWN, ""


def guess_config_lang(lower_name: str) -> str:
    """为特殊配置文件推断一个用于注释统计与展示的语言名。"""
    if lower_name in ("makefile", "gnumakefile", "sconstruct", "justfile", "taskfile"):
        return "Makefile"
    if lower_name in ("dockerfile", "containerfile"):
        return "Dockerfile"
    if lower_name == "cmakelists.txt":
        return "CMake"
    if lower_name.endswith(".gradle") or lower_name in ("build.bazel", "bazelrc"):
        return "Gradle"
    # 依赖清单：requirements.txt / Pipfile / Gemfile / go.mod / Cargo.lock 等
    if lower_name.startswith("requirements") or lower_name in (
        "pipfile", "pipfile.lock", "gemfile", "gemfile.lock",
        "go.mod", "go.sum", "cargo.lock", "poetry.lock", "yarn.lock",
        "pnpm-lock.yaml", "package-lock.json", "brewfile",
    ):
        return "Dotenv" if lower_name != "cargo.lock" else "TOML"
    if lower_name.endswith(".json"):
        return "JSON"
    if lower_name.endswith((".yaml", ".yml")):
        return "YAML"
    if lower_name.endswith(".toml"):
        return "TOML"
    if lower_name.endswith(".xml"):
        return "XML"
    if lower_name.endswith((".cfg", ".ini")):
        return "INI"
    if lower_name.endswith(".lock"):
        return "TOML"
    # .gitignore / .dockerignore / .npmrc / .flake8 等，均以 # 注释
    return "Dotenv"


# --------------------------------------------------------------------------
# 二进制内容探测
# --------------------------------------------------------------------------
BINARY_SNIFF_BYTES = 8192
_BINARY_THRESHOLD = 0.30


def looks_binary(path: str) -> bool:
    """读文件头部判断是否为二进制。

    判据：含 NUL 字节，或不可解码字节占比超过 30%。
    读取失败时返回 True（当作无法统计处理，调用方会记入错误清单）。
    """
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(BINARY_SNIFF_BYTES)
    except OSError:
        return True

    if not chunk:
        return False  # 空文件按文本处理

    if b"\x00" in chunk:
        return True

    # 统计控制字符（排除 \t \n \r \f \v 这些合法文本字符）占比
    bad = sum(
        1 for byte in chunk
        if byte < 0x09 or (0x0E <= byte < 0x20) or byte == 0x7F
    )
    return (bad / len(chunk)) > _BINARY_THRESHOLD