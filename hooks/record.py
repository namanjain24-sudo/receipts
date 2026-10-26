#!/usr/bin/env python3
"""PostToolUse: write down what the tools actually did.

This runs after every Bash / Edit / Write / NotebookEdit call and appends one
line to the session ledger. It never blocks and never fails the turn -- any
error here exits 0 silently, because a broken bookkeeper must not break the
session it is keeping books for.
"""
import json, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import checks, ledger, paths  # noqa: E402

MUTATING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

#: bash that changes files on disk -- these count as mutations too, otherwise
#: `sed -i` and `>` would quietly reset the staleness clock without anyone
#: noticing the code moved under the last test run.
WRITES = [
    r"\bsed\s+-i\b",
    r"\b(cp|mv|rm|mkdir|touch|truncate|tee|dd)\s",
    r"\bgit\s+(checkout|restore|apply|revert|stash|reset|merge|rebase|cherry-pick)\b",
    r"\bpatch\s+-",
    r"\b(prettier|biome|black|isort|rustfmt|gofmt|clang-format)\b[^|&;]*\s(--write|-w|-i)\b",
    r"\b(cargo\s+fmt|go\s+fmt|ruff\s+format|npm\s+run\s+format)\b",
    r"\b(npm|yarn|pnpm|bun|pip3?|poetry|cargo|go)\s+(install|add|get|remove|uninstall)\b",
    # a redirect into a real file, but not into a throwaway one -- writing a
    # test log to /tmp is not a code change, and treating it as one would make
    # a perfectly fresh green run look stale.
    r">>?\s*(?!/dev/null)(?!/tmp/)(?!/private/tmp/)(?!/var/folders/)[^\s|&;<>]*\.[A-Za-z0-9]+",
]


def main():
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw)
    except ValueError:
        return
    sid = ev.get("session_id")
    tool = ev.get("tool_name") or ""
    tin = ev.get("tool_input") or {}
    tout = ev.get("tool_response")
    if not isinstance(tout, dict):
        tout = {}

    if tool in MUTATING_TOOLS:
        f = tin.get("file_path") or tin.get("notebook_path") or "?"
        ledger.append(sid, {
            "kind": "mutation", "tool": tool, "file": f,
            "source": paths.is_source(f),
        })
        return

    if tool != "Bash":
        return

    cmd = str(tin.get("command") or "")
    if not cmd.strip():
        return

    # A backgrounded command has not produced its result yet, so it proves
    # nothing at this point.
    if tout.get("backgroundTaskId"):
        return

    fam = checks.family(cmd)
    if fam:
        v, passed, failed, why = checks.verdict(
            cmd, tout.get("stdout"), tout.get("stderr"), tout.get("interrupted"))
        ledger.append(sid, {
            "kind": "check", "family": fam, "cmd": cmd[:400],
            "verdict": v, "passed": passed, "failed": failed, "why": why,
            "cwd": ev.get("cwd"),
        })

    if any(re.search(p, cmd) for p in WRITES):
        # Only a shell write we can pin to a source file resets the staleness
        # clock. `rm /tmp/x` and `mkdir build` are housekeeping, and counting
        # them was the noisiest rule when replaying real sessions.
        targets = [t for t in paths.bash_targets(cmd) if paths.is_source(t)]
        ledger.append(sid, {
            "kind": "mutation", "tool": "Bash",
            "file": targets[0] if targets else cmd[:120],
            "source": bool(targets),
        })


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
