"""Classify a shell command, and read a verdict out of what it actually printed.

Claude Code does not hand hooks an exit code: a PostToolUse payload for Bash
carries only stdout, stderr, interrupted, isImage and noOutputExpected. So a
verdict has to come from parsing the output. Every parser here returns
"unknown" when it is not sure -- unknown is never treated as a pass, because
the whole point is to stop optimistic reporting.
"""
import re

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"

# (family, pattern). First match wins, so order matters: a `npm test` line must
# be seen as a test before the generic npm-script rule calls it a build.
FAMILIES = [
    ("test",   r"\b(pytest|py\.test|tox|nose2)\b"),
    ("test",   r"\b(jest|vitest|mocha|ava|jasmine|karma)\b"),
    ("test",   r"\bgo\s+test\b"),
    ("test",   r"\bcargo\s+(test|nextest)\b"),
    ("test",   r"\b(rspec|minitest|phpunit|pest)\b"),
    ("test",   r"\bdotnet\s+test\b"),
    ("test",   r"\b(mvn|gradle|gradlew)\b.*\btest\b"),
    ("test",   r"\b(npm|yarn|pnpm|bun)\s+(run\s+)?test\b"),
    ("test",   r"\bbun\s+test\b"),
    ("test",   r"\bctest\b"),
    ("build",  r"\b(tsc|swc)\b"),
    ("build",  r"\b(cargo|go)\s+build\b"),
    ("build",  r"\b(make|cmake|ninja|bazel)\b"),
    ("build",  r"\b(vite|webpack|rollup|esbuild|parcel)\s+build\b"),
    ("build",  r"\b(next|nuxt|astro|remix)\s+build\b"),
    ("build",  r"\b(npm|yarn|pnpm|bun)\s+(run\s+)?build\b"),
    ("build",  r"\bdocker\s+build\b"),
    ("lint",   r"\b(eslint|biome|oxlint|standard)\b"),
    ("lint",   r"\b(ruff|flake8|pylint|black|isort)\b"),
    ("lint",   r"\b(mypy|pyright|pyre)\b"),
    ("lint",   r"\bcargo\s+(clippy|fmt)\b"),
    ("lint",   r"\bgo\s+vet\b"),
    ("lint",   r"\bprettier\b.*--check"),
    ("lint",   r"\b(npm|yarn|pnpm|bun)\s+(run\s+)?(lint|typecheck|type-check|tsc)\b"),
    ("http",   r"\b(curl|wget|http|httpie|xh)\b"),
    ("vcs",    r"\bgit\s+(log|status|show|diff|rev-parse|branch)\b"),
    ("vcs",    r"\bgh\s+(pr|run|workflow|release)\b"),
    ("vcs",    r"\bgit\s+(push|merge|commit)\b"),
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


def family(command):
    """Name the verification family of a shell command, or None."""
    c = " " + re.sub(r"\s+", " ", command or "") + " "
    for name, pat in FAMILIES:
        if re.search(pat, c, re.I):
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

    if fam in ("build", "lint") and re.search(r"\b(tsc|typecheck|type-check)\b", command, re.I):
        v, p, f = _tsc(text)
        if v != UNKNOWN:
            return v, p, f, "parsed tsc diagnostics"

    if fam == "http":
        m = re.search(r"\bHTTP/[\d.]+\s+(\d{3})", text)
        if m:
            code = int(m.group(1))
            return (PASS if code < 400 else FAIL), None, None, "HTTP %d" % code

    for pat in GENERIC_FAIL:
        m = re.search(pat, text, re.I | re.M)
        if m:
            return FAIL, None, None, "output matched %r" % m.group(0).strip()[:60]

    # Nothing said it failed. That is not the same as proof that it passed,
    # except for families whose silence is meaningful.
    if fam in ("build", "lint") and text.strip():
        return PASS, None, None, "%s produced no failure output" % fam
    if fam == "test":
        return UNKNOWN, None, None, "could not find a test summary in the output"
    return UNKNOWN, None, None, "no rule matched"
