"""Does this path look like code that a check would care about?

The staleness rule asks whether anything changed since the last green run.
Replaying real sessions, most of what tripped it was housekeeping -- a temp
file removed, a build directory made, a README touched -- none of which can
turn a passing test red. Narrowing "mutation" to source files is what makes
the rule usable.
"""
import os, re

#: edits to these cannot change what a test or build does
INERT_EXT = {
    ".md", ".mdx", ".rst", ".txt", ".log", ".csv", ".tsv", ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".mp4", ".mov",
    ".lock", ".sum", ".map", ".min.js", ".snap",
}

INERT_NAMES = {"license", "licence", "readme", "changelog", "contributing",
               "code_of_conduct", "authors", "notice", ".gitignore",
               ".gitattributes", ".editorconfig", ".dockerignore"}

#: anything under these is generated, vendored or scratch
INERT_DIRS = re.compile(
    r"(^|/)(\.git|node_modules|\.venv|venv|__pycache__|\.pytest_cache|\.mypy_cache|"
    r"\.ruff_cache|dist|build|out|target|coverage|\.next|\.nuxt|\.cache|"
    r"vendor|Pods|\.gradle|site-packages|\.tox)(/|$)")

TEMP_ROOTS = ("/tmp/", "/private/tmp/", "/var/folders/", "/dev/")


def is_source(path):
    """True when editing `path` could plausibly change a check's result."""
    if not path:
        return False
    p = str(path).strip().strip("'\"")
    if not p or p.startswith("-"):
        return False
    low = p.lower()
    if low.startswith(TEMP_ROOTS) or "/tmp/" in low:
        return False
    if INERT_DIRS.search(low):
        return False
    base = os.path.basename(low)
    stem, ext = os.path.splitext(base)
    if ext in INERT_EXT or base.endswith(".min.js"):
        return False
    if stem in INERT_NAMES or base in INERT_NAMES:
        return False
    return True


#: a destination argument, a redirect target, or the file sed rewrites
TARGET_PATTERNS = [
    r">>?\s*([^\s|&;<>]+)",
    r"\b(?:cp|mv|install)\s+(?:-\S+\s+)*\S+\s+(\S+)",
    r"\b(?:rm|touch|truncate)\s+(?:-\S+\s+)*(\S+)",
    r"\btee\s+(?:-\S+\s+)*(\S+)",
]


def bash_targets(command):
    """Best-effort list of files a shell command writes to."""
    out = []
    # `sed -i` rewrites its last argument, and BSD sed takes an extra empty
    # backup suffix (`sed -i '' s/a/b/ file`), so the last token is the file.
    for m in re.finditer(r"\bsed\s+(?:-\S+\s+)*[^|&;\n]+", command or ""):
        toks = m.group(0).split()
        if len(toks) > 1 and not toks[-1].startswith("-"):
            out.append(toks[-1])
    for pat in TARGET_PATTERNS:
        for m in re.finditer(pat, command or ""):
            t = m.group(1)
            if t and not t.startswith("-"):
                out.append(t)
    return out
