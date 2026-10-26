"""Classify a shell command, and read a verdict out of what it actually printed.

Claude Code does not hand hooks an exit code: a PostToolUse payload for Bash
carries only stdout, stderr, interrupted, isImage and noOutputExpected. So a
verdict has to come from parsing the output. Every parser here returns
"unknown" when it is not sure -- unknown is never treated as a pass, because
the whole point is to stop optimistic reporting.
"""
import re

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"

# (family, program, args). `program` is matched against the command's first
# token and `args` against the rest, so `echo "curl is great"` is not an HTTP
# check and `cd /x && cp a.csv /tmp` is not anything at all. First match wins.
FAMILIES = [
    ("test",  r"^(pytest|py\.test|tox|nose2)$", None),
    ("test",  r"^(jest|vitest|mocha|ava|jasmine|karma|rspec|minitest|phpunit|pest)$", None),
    ("test",  r"^go$",      r"^test\b"),
    ("test",  r"^cargo$",   r"^(test|nextest)\b"),
    ("test",  r"^dotnet$",  r"^test\b"),
    ("test",  r"^(mvn|gradle|gradlew|\./gradlew)$", r"\btest\b"),
    ("test",  r"^(npm|yarn|pnpm|bun)$", r"^(run\s+)?test\b"),
    ("test",  r"^bun$",     r"^test\b"),
    ("test",  r"^ctest$",   None),
    ("test",  r"^(\./)?manage\.py$", r"^test\b"),
    # a script whose own name says it is a test -- `python3 tests/test_x.py`,
    # `./run-tests.sh`, `node spec/index.js`. Checked after the real runners.
    ("test",  r"^[\w./~-]*(test|spec)[\w./-]*\.(py|js|mjs|cjs|ts|sh|rb|pl)$", None),
    ("test",  r"^\.{0,2}/[\w./-]*(run-?tests?|tests?|specs?)(\.sh)?$", None),
    ("test",  r"^deno$",    r"^test\b"),
    ("test",  r"^(mix|rebar3)$", r"^(test|eunit|ct)\b"),

    # a typecheck is a lint, even when the same binary can also build
    ("lint",  r"^tsc$",     r"--noEmit"),
    ("lint",  r"^(eslint|biome|oxlint|standard|ruff|flake8|pylint|black|isort)$", None),
    ("lint",  r"^(mypy|pyright|pyre|ty)$", None),
    ("lint",  r"^cargo$",   r"^(clippy|fmt)\b"),
    ("lint",  r"^go$",      r"^vet\b"),
    ("lint",  r"^prettier$", r"--check"),
    ("lint",  r"^(npm|yarn|pnpm|bun)$", r"^(run\s+)?(lint|typecheck|type-check|tsc)\b"),

    ("build", r"^(tsc|swc)$", None),
    ("build", r"^(cargo|go)$", r"^build\b"),
    ("build", r"^(make|cmake|ninja|bazel)$", None),
    ("build", r"^(vite|webpack|rollup|esbuild|parcel|next|nuxt|astro|remix)$", r"^build\b"),
    ("build", r"^(npm|yarn|pnpm|bun)$", r"^(run\s+)?build\b"),
    ("build", r"^docker$",  r"^build\b"),
    ("build", r"^(mvn|gradle|gradlew|\./gradlew)$", r"\b(package|assemble|build)\b"),

    ("http",  r"^(curl|wget|http|httpie|xh)$", None),

    # read-only git/gh only -- `git push` is an action, and whether it worked
    # is read from a later `git log` or from gh, not from the push itself
    ("vcs",   r"^git$", r"^(log|status|show|diff|rev-parse|branch)\b"),
    ("vcs",   r"^gh$",  r"^(pr|run|workflow|release|issue)\b"),
]

#: which families can back which kind of claim
CLAIM_EVIDENCE = {
    "tests":    {"test"},
    "builds":   {"build", "test"},
    "lints":    {"lint", "build"},
    "deployed": {"http", "vcs"},
    "merged":   {"vcs"},
    "works":    {"test", "build", "http"},
    "done":     {"test", "build", "lint", "http", "vcs"},
}


#: wrappers that run the real program as their argument
WRAPPERS = {"npx", "pnpm", "yarn", "npm", "bun", "bunx", "uv", "uvx", "poetry",
            "pipenv", "rye", "pdm", "hatch", "nix-shell", "docker", "time",
            "env", "sudo", "nohup", "xargs", "command", "exec",
            "python", "python3", "py", "node", "bash", "sh", "zsh", "ruby"}
#: wrapper sub-words that carry no meaning of their own
WRAPPER_NOISE = {"run", "exec", "x", "--", "-m", "--silent", "-s", "--yes", "-y"}


def _segments(command):
    """Split a shell line into the simple commands it is made of.

    Regex-matching the whole string is how `cd /x && cp a.csv /tmp` ends up
    classified as an HTTP check because something later in the line contains
    a URL. Only the program actually being run should decide the family.
    """
    text = re.sub(r"<<-?\s*'?\w+'?.*", " ", command or "", flags=re.S)  # drop heredoc bodies
    parts = re.split(r"\|\||&&|[;|\n]", text)
    out = []
    for part in parts:
        toks = part.strip().split()
        # skip leading env assignments and `cd somewhere`
        while toks and ("=" in toks[0].split("/")[0] and not toks[0].startswith("-")):
            toks = toks[1:]
        if toks and toks[0] in ("cd", "pushd") and len(toks) > 1:
            toks = toks[2:]
        # unwrap `npx jest`, `uv run pytest`, `pnpm exec tsc`
        while toks and toks[0] in WRAPPERS:
            rest = toks[1:]
            while rest and rest[0] in WRAPPER_NOISE:
                rest = rest[1:]
            if not rest:
                break
            # `npm test` / `npm run build` keep their meaning, so stop there
            if toks[0] in ("npm", "yarn", "pnpm", "bun") and rest[0] in (
                    "test", "build", "lint", "typecheck", "type-check", "install", "add"):
                toks = [toks[0], rest[0]]
                break
            toks = rest
        if toks:
            out.append(" ".join(toks))
    return out


#: tools that rewrite files unless told otherwise. They are changes, not
#: checks, and treating them as checks made a green run look stale against
#: its own reformatting.
WRITE_BY_DEFAULT = re.compile(
    r"^(black|isort|rustfmt|gofmt|clang-format|yapf|autopep8|dprint)$", re.I)
WRITE_BY_DEFAULT_SUB = re.compile(
    r"^(ruff\s+format|cargo\s+fmt|go\s+fmt|dart\s+format|mix\s+format)\b", re.I)
#: tools that only rewrite when asked
WRITE_FLAG = re.compile(r"(^|\s)(--write|--fix|-w|--in-place)(\s|$)", re.I)
#: ... and the flags that turn a rewriter back into a check
CHECK_FLAG = re.compile(r"(^|\s)(--check|--diff|--dry-run|--list-different|-l)(\s|$)", re.I)


def _rewrites_files(seg):
    """True when this segment edits files rather than reporting on them."""
    toks = seg.split()
    if not toks:
        return False
    if WRITE_FLAG.search(seg) and not CHECK_FLAG.search(seg):
        return True
    rest = " ".join(toks[1:])
    if WRITE_BY_DEFAULT.match(toks[0]) or WRITE_BY_DEFAULT_SUB.match(seg):
        return not CHECK_FLAG.search(rest)
    return False


def family(command):
    """Name the verification family of a shell command, or None.

    The family comes from the program being run, not from anything that
    happens to appear elsewhere on the line.
    """
    for seg in _segments(command):
        toks = seg.split()
        if not toks:
            continue
        if _rewrites_files(seg):
            continue
        prog, args = toks[0], " ".join(toks[1:])
        for name, prog_pat, arg_pat in FAMILIES:
            if not re.match(prog_pat, prog, re.I):
                continue
            if arg_pat and not re.search(arg_pat, args, re.I):
                continue
            return name
    return None


# Each parser gets the combined stdout+stderr and returns
# (verdict, passed, failed) with None for counts it cannot determine.
def _pytest(t):
    m = re.search(r"={3,}\s*(.*?)\s*(?:in\s+[\d.]+s.*?)?={3,}\s*$", t.strip(), re.M)
    tail = m.group(1) if m else t[-400:]
    failed = _num(tail, r"(\d+)\s+failed") or 0
    errors = _num(tail, r"(\d+)\s+error") or 0
    passed = _num(tail, r"(\d+)\s+passed")
    if failed or errors:
        return FAIL, passed, failed + errors
    if passed is not None or re.search(r"\bno tests ran\b", tail, re.I):
        if passed:
            return PASS, passed, 0
        return UNKNOWN, passed, 0  # "no tests ran" proves nothing
    return UNKNOWN, None, None


def _jest(t):
    m = re.search(r"^Tests:\s+(.*)$", t, re.M)
    line = m.group(1) if m else ""
    failed = _num(line, r"(\d+)\s+failed")
    passed = _num(line, r"(\d+)\s+passed")
    if failed:
        return FAIL, passed, failed
    if failed == 0 and passed:
        return PASS, passed, 0
    if re.search(r"^Test Suites:.*\bfailed\b", t, re.M):
        return FAIL, passed, failed
    return UNKNOWN, passed, failed


def _gotest(t):
    if re.search(r"^(FAIL|--- FAIL)", t, re.M):
        return FAIL, None, len(re.findall(r"^--- FAIL", t, re.M)) or None
    if re.search(r"^(ok|PASS)\b", t, re.M):
        return PASS, len(re.findall(r"^--- PASS", t, re.M)) or None, 0
    if re.search(r"no test files", t, re.I):
        return UNKNOWN, None, None
    return UNKNOWN, None, None


def _cargo(t):
    m = re.search(r"test result:\s*(ok|FAILED)\.\s*(\d+)\s+passed;\s*(\d+)\s+failed", t)
    if m:
        ok, p, f = m.group(1) == "ok", int(m.group(2)), int(m.group(3))
        return (PASS if ok and f == 0 else FAIL), p, f
    if re.search(r"^error(\[|\:)", t, re.M):
        return FAIL, None, None
    return UNKNOWN, None, None


def _mocha(t):
    passed = _num(t, r"(\d+)\s+passing")
    failed = _num(t, r"(\d+)\s+failing")
    if failed:
        return FAIL, passed, failed
    if passed:
        return PASS, passed, 0
    return UNKNOWN, passed, failed


def _plain(t):
    """`26 passed, 0 failed` -- what hand-rolled runners usually print."""
    failed = _num(t, r"(\d+)\s+(?:tests?\s+)?failed\b")
    passed = _num(t, r"(\d+)\s+(?:tests?\s+)?passed\b")
    errors = _num(t, r"(\d+)\s+errors?\b")
    if failed is None and passed is None:
        return UNKNOWN, None, None
    if (failed or 0) + (errors or 0) > 0:
        return FAIL, passed, (failed or 0) + (errors or 0)
    if passed:
        return PASS, passed, 0
    return UNKNOWN, passed, failed


def _tsc(t):
    n = _num(t, r"Found\s+(\d+)\s+error")
    if n is not None:
        return (PASS if n == 0 else FAIL), None, n
    if re.search(r"error TS\d+", t):
        return FAIL, None, len(set(re.findall(r"^(.*?error TS\d+)", t, re.M))) or None
    return UNKNOWN, None, None


TEST_PARSERS = [
    ("pytest", r"\b(pytest|py\.test|tox)\b", _pytest),
    ("jest",   r"\b(jest|vitest)\b",          _jest),
    ("go test", r"\bgo\s+test\b",            _gotest),
    ("cargo test", r"\bcargo\s+(test|nextest)\b", _cargo),
    ("mocha",  r"\b(mocha|ava|rspec)\b",      _mocha),
]


def _num(text, pat):
    m = re.search(pat, text or "", re.I)
    return int(m.group(1)) if m else None


#: lines that mean failure no matter which tool printed them
GENERIC_FAIL = [
    r"^\s*(FAILED|FAIL|ERROR)\b",
    r"\b\d+\s+(test|spec)s?\s+failed\b",
    r"^error(\[[A-Z0-9]+\])?:",
    r"\berror TS\d+\b",
    r"^\s*Traceback \(most recent call last\)",
    r"\bsegmentation fault\b",
    r"\bcommand not found\b",
    r"\bno such file or directory\b",
    r"\bModuleNotFoundError\b",
    r"\bnpm ERR!",
    r"\bBuild failed\b",
    r"\bcompilation (failed|error)\b",
]


def verdict(command, stdout, stderr, interrupted=False):
    """Read a pass/fail/unknown verdict out of what a command printed.

    Returns (verdict, passed, failed, why) where `why` names the rule that
    decided it, so a blocked turn can explain itself instead of just refusing.
    """
    text = (stdout or "") + "\n" + (stderr or "")
    if interrupted:
        return UNKNOWN, None, None, "command was interrupted"

    fam = family(command)
    if fam == "test":
        for label, pat, parser in TEST_PARSERS:
            if re.search(pat, command, re.I):
                v, p, f = parser(text)
                if v != UNKNOWN:
                    return v, p, f, "parsed the %s summary line" % label
                break
        else:
            v, p, f = _plain(text)
            if v != UNKNOWN:
                return v, p, f, "parsed a plain pass/fail summary"

    if fam in ("build", "lint") and re.search(r"\b(tsc|typecheck|type-check)\b", command, re.I):
        v, p, f = _tsc(text)
        if v != UNKNOWN:
            return v, p, f, "parsed tsc diagnostics"

    if fam == "http":
        m = re.search(r"\bHTTP/[\d.]+\s+(\d{3})", text)
        if m:
            code = int(m.group(1))
            return (PASS if code < 400 else FAIL), None, None, "HTTP %d" % code

    # Generic failure sniffing is only applied to families whose output we
    # understand. Applied to everything it reads an "error:" inside a log line
    # or a JSON field as a failed command, which is how `cp results.csv` once
    # came back as a failing HTTP check.
    if fam in ("test", "build", "lint"):
        for pat in GENERIC_FAIL:
            m = re.search(pat, text, re.I | re.M)
            if m:
                return FAIL, None, None, "output matched %r" % m.group(0).strip()[:60]

    # Nothing said it failed. That is not the same as proof that it passed,
    # except for families whose silence is meaningful.
    if fam in ("build", "lint") and text.strip():
        return PASS, None, None, "%s produced no failure output" % fam
    if fam == "test":
        v, p, f = _plain(text)
        if v != UNKNOWN:
            return v, p, f, "parsed a plain pass/fail summary"
        return UNKNOWN, None, None, "could not find a test summary in the output"
    return UNKNOWN, None, None, "no rule matched"
