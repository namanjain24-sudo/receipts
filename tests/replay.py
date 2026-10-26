#!/usr/bin/env python3
"""Measure how often the gate would fire on your own past sessions.

Claude Code keeps every session as a JSONL transcript under
~/.claude/projects. This replays each turn through the real gate -- same
classifier, same parsers, same policy -- with a ledger rebuilt from the
transcript, and reports how often it would have blocked.

Run it before trusting the gate, and again after changing any rule:

    python3 tests/replay.py                 # your own sessions
    python3 tests/replay.py --samples 10    # ... and show what it caught
    python3 tests/replay.py --root DIR      # a different transcript folder
"""
import argparse, json, os, re, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
import checks, paths  # noqa: E402

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("gate", os.path.join(ROOT, "hooks", "gate.py"))
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

MUT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
WRITES = [
    r"\bsed\s+-i\b", r"\b(cp|mv|rm|mkdir|touch|truncate|tee|dd)\s",
    r"\bgit\s+(checkout|restore|apply|revert|stash|reset|merge|rebase|cherry-pick)\b",
    r"\bpatch\s+-",
    r"\b(prettier|biome|black|isort|rustfmt|gofmt|clang-format)\b[^|&;]*\s(--write|-w|-i)\b",
    r"\b(cargo\s+fmt|go\s+fmt|ruff\s+format|npm\s+run\s+format)\b",
    r"\b(npm|yarn|pnpm|bun|pip3?|poetry|cargo|go)\s+(install|add|get|remove|uninstall)\b",
    r">>?\s*(?!/dev/null)(?!/tmp/)(?!/private/tmp/)(?!/var/folders/)[^\s|&;<>]*\.[A-Za-z0-9]+",
]
CFG = {"locales": ["en", "hinglish"], "enabled": True,
       "checks": ["failing", "stale", "subagent", "missing", "inert"]}


def _is_human(d):
    c = (d.get("message") or {}).get("content")
    return d.get("toolUseResult") is None and (
        isinstance(c, str) or (isinstance(c, list) and any(
            isinstance(b, dict) and b.get("type") == "text" for b in c)))


def replay_file(path):
    """Yield (final_message, ledger) for every turn in one transcript."""
    rows = []
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return []
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass

    turns, led, buf, last_req, pend, seq = [], [], [], None, {}, [0]

    def add(e):
        e["seq"] = seq[0]; seq[0] += 1; led.append(e)

    for d in rows:
        t = d.get("type")
        c = (d.get("message") or {}).get("content")
        if t == "user":
            if _is_human(d):
                if buf:
                    turns.append(("\n".join(buf), list(led)))
                buf, last_req = [], None
                continue
            if isinstance(c, list):
                for b in c:
                    if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                        continue
                    inp = pend.pop(b.get("tool_use_id"), None)
                    if inp is None:
                        continue
                    r = d.get("toolUseResult")
                    so = r.get("stdout", "") if isinstance(r, dict) else ""
                    se = r.get("stderr", "") if isinstance(r, dict) else ""
                    cmd = str(inp.get("command", ""))
                    fam = checks.family(cmd)
                    if fam:
                        v, p, f, why = checks.verdict(cmd, so, se, False)
                        add({"kind": "check", "family": fam, "cmd": cmd[:200],
                             "verdict": v, "passed": p, "failed": f, "why": why})
                    if any(re.search(w, cmd) for w in WRITES):
                        tg = [x for x in paths.bash_targets(cmd) if paths.is_source(x)]
                        add({"kind": "mutation", "tool": "Bash",
                             "file": tg[0] if tg else cmd[:120], "source": bool(tg)})
            continue
        if t != "assistant":
            continue
        msg = d.get("message") or {}
        req = d.get("requestId") or msg.get("id")
        blocks = [{"type": "text", "text": c}] if isinstance(c, str) else (
            c if isinstance(c, list) else [])
        for b in blocks:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                continue
            n, inp = b.get("name"), (b.get("input") or {})
            if n in MUT_TOOLS:
                fp = str(inp.get("file_path", "?"))
                add({"kind": "mutation", "tool": n, "file": fp,
                     "source": paths.is_source(fp)})
            elif n == "Bash":
                pend[b.get("id")] = inp
        texts = [b.get("text", "") for b in blocks
                 if isinstance(b, dict) and b.get("type") == "text"]
        if not any(x.strip() for x in texts):
            continue
        if req != last_req:
            last_req, buf = req, []
        buf.extend(texts)
    if buf:
        turns.append(("\n".join(buf), list(led)))
    return turns


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--samples", type=int, default=0,
                    help="print this many of the blocks, to judge them yourself")
    a = ap.parse_args()

    if not os.path.isdir(a.root):
        print("No transcripts at %s" % a.root)
        return 1

    turns = blocked = 0
    tags = Counter()
    shown = []
    for dirpath, _, names in os.walk(a.root):
        for n in names:
            if not n.endswith(".jsonl"):
                continue
            for msg, led in replay_file(os.path.join(dirpath, n)):
                turns += 1
                probs = gate.evaluate(msg, led, [], CFG)
                if not probs:
                    continue
                blocked += 1
                tags[probs[0][0]] += 1
                if len(shown) < a.samples:
                    shown.append(probs[0])

    if not turns:
        print("No turns found under %s" % a.root)
        return 1
    print("turns replayed: %d" % turns)
    print("would block:    %d  (%.1f%%)\n" % (blocked, 100.0 * blocked / turns))
    for k, v in tags.most_common():
        print("  %-9s %5d  (%.1f%% of turns)" % (k, v, 100.0 * v / turns))
    for tag, _kind, sentence, why in shown:
        print("\n  [%s] %s\n        %s" % (tag, sentence.strip()[:100], why[:140]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
