#!/usr/bin/env python3
"""Stop hook: don't let the turn end on a claim nothing in it supports.

Exit 2 with a reason on stderr blocks the stop and hands the reason back to
the model. Every other path exits 0, including every error path -- a gate that
wedges a session is worse than no gate.

Five ways a "done" goes wrong, in the order they are worth reporting:
  1. the evidence that exists says it FAILED   (loudest, and often unread)
  2. the evidence is STALE -- code changed after the last check ran
  3. a SUBAGENT reported failure that the summary dropped
  4. the test is INERT -- green with or without the change (needs /prove)
  5. there is NO evidence of the claimed kind at all

The vague claim kinds ("done", "it works") only count when the session
actually changed something. Replaying 1,352 real turns, 38% of all firings
were in turns that edited nothing at all -- a "scan ho gaya" or "pattern
confirmed" is a claim about reading, not about code, and there is nothing
for it to prove.
"""
import json, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import checks, claims, ledger, transcript  # noqa: E402

#: families whose output we can actually read, so an unreadable result there
#: is suspicious. A `git log` or a `curl` that we could not parse is ordinary,
#: and blocking on it was the single noisiest rule when replaying real turns.
READABLE = {"test", "build", "lint"}

#: claims naming a concrete, falsifiable action. These are worth checking even
#: in a session that changed no files -- "tests pass" stands on its own.
#: The vague ones ("done", "it works") need a code change to be about code.
SPECIFIC_KINDS = {"tests", "builds", "lints", "deployed", "merged"}

SUBAGENT_FAIL = [
    r"\b\d+\s+(tests?|specs?|checks?)\s+failed\b",
    r"^\s*(FAILED|FAIL|ERROR)\b",
    r"\bcould\s+not\s+(complete|finish|fix)\b",
    r"\bblocked\s+by\b",
    r"\bI\s+was\s+unable\s+to\b",
]

INERT_HINT = ("write a test that fails when your change is reverted, then run it")

NEXT_STEP = {
    "tests": "run the project's test command and read the summary line",
    "builds": "run the build and read its output",
    "lints": "run the linter or typechecker",
    "deployed": "curl the deployed URL, or check `gh run view`",
    "merged": "run `git log --oneline -3` or `git status`",
    "works": "run the smallest check that would fail if the claim were false",
    "done": "run the smallest check that would fail if the claim were false",
}


def load_config(cwd):
    cfg = {"locales": ["en"], "enabled": True, "checks": ["failing", "stale", "subagent", "missing", "inert"]}
    for name in (".claude/receipts.json", "receipts.json"):
        p = os.path.join(cwd or ".", name)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    cfg.update(json.load(fh))
            except (OSError, ValueError):
                pass
            break
    return cfg


def describe(entry):
    bits = []
    if entry.get("failed"):
        bits.append("%s failed" % entry["failed"])
    if entry.get("passed") is not None:
        bits.append("%s passed" % entry["passed"])
    tail = " (%s)" % ", ".join(bits) if bits else ""
    return "`%s`%s" % (entry.get("cmd", "?")[:120], tail)


def evaluate(final_text, entries, subagent_texts, cfg):
    """Return a list of problems worth blocking on."""
    problems = []
    found = claims.find(final_text, tuple(cfg.get("locales", ["en"])))
    if not found:
        return problems

    enabled = set(cfg.get("checks", []))
    # staleness only cares about source: a README edit cannot turn a green
    # test red, and counting it was the noisiest rule on real sessions.
    last_mut = ledger.last_mutation(entries, source_only=True)
    any_mut = ledger.last_mutation(entries)
    seen_kinds = set()

    # A vague claim in a session that changed nothing is a claim about
    # reading, not about code. Measured on 1,352 real turns, dropping these
    # removes 38% of all firings and none of the real ones.
    vague_ok = any_mut is not None

    for kind, sentence in found:
        if kind in seen_kinds:
            continue
        seen_kinds.add(kind)
        families = checks.CLAIM_EVIDENCE.get(kind, set())
        relevant = ledger.checks_for(entries, families)

        failing = [e for e in relevant if e.get("verdict") == checks.FAIL]
        passing = [e for e in relevant if e.get("verdict") == checks.PASS]

        if failing and "failing" in enabled:
            latest_fail = failing[-1]
            if not passing or passing[-1]["seq"] < latest_fail["seq"]:
                problems.append((
                    "failing", kind, sentence,
                    "the last %s check reported a failure: %s"
                    % (latest_fail.get("family"), describe(latest_fail))))
                continue

        if kind in ("tests", "works", "done") and "inert" in enabled:
            inerts = [e for e in entries if e.get("kind") == "inert"]
            if inerts and inerts[-1].get("status") == "INERT":
                problems.append((
                    "inert", kind, sentence,
                    "the test that backs this passes at HEAD too, so it is green "
                    "with or without the change (`%s`)" % inerts[-1].get("cmd", "?")[:100]))
                continue

        if not relevant and "missing" in enabled:
            if kind not in SPECIFIC_KINDS and not vague_ok:
                continue  # nothing was changed, so there is nothing to prove
            problems.append((
                "missing", kind, sentence,
                "nothing in this session ran a %s check"
                % "/".join(sorted(families))))
            continue

        if passing and last_mut and "stale" in enabled:
            if passing[-1]["seq"] < last_mut["seq"]:
                problems.append((
                    "stale", kind, sentence,
                    "the last passing check was %s, and %s changed after it"
                    % (describe(passing[-1]), last_mut.get("file", "code")[:80])))
                continue

        unreadable = [e for e in relevant if e.get("family") in READABLE]
        if not passing and unreadable and "missing" in enabled:
            if kind not in SPECIFIC_KINDS and not vague_ok:
                continue
            unknown = unreadable[-1]
            problems.append((
                "missing", kind, sentence,
                "a %s command ran but its result could not be read (%s)"
                % (unknown.get("family"), unknown.get("why", "no summary found"))))

    if "subagent" in enabled:
        for text in subagent_texts:
            for pat in SUBAGENT_FAIL:
                m = re.search(pat, text, re.I | re.M)
                if m:
                    problems.append((
                        "subagent", found[0][0], found[0][1],
                        "a subagent reported %r, which the summary does not mention"
                        % m.group(0).strip()[:80]))
                    break
            else:
                continue
            break

    return problems


def render(problems):
    head = ("Receipts: %d claim%s in this message %s not backed by what actually ran."
            % (len(problems), "" if len(problems) == 1 else "s",
               "is" if len(problems) == 1 else "are"))
    lines = [head, ""]
    for tag, kind, sentence, why in problems:
        lines.append("  [%s] you wrote: %s" % (tag, sentence.strip()))
        lines.append("           but %s." % why)
        step = INERT_HINT if tag == "inert" else NEXT_STEP.get(kind, "verify it")
        lines.append("           -> %s, then say what it printed." % step)
        lines.append("")
    lines.append("Either produce the check, or rewrite the claim to match what you "
                 "actually know. Saying it is unverified is a fine answer.")
    return "\n".join(lines)


def main():
    try:
        ev = json.loads(sys.stdin.read())
    except ValueError:
        return 0
    if ev.get("stop_hook_active"):
        return 0

    cfg = load_config(ev.get("cwd"))
    if not cfg.get("enabled", True):
        return 0

    tpath = ev.get("transcript_path") or ""
    final_text = transcript.final_message(tpath) if tpath else ""
    if not final_text:
        return 0

    entries = ledger.read(ev.get("session_id"))
    subs = transcript.subagent_results(tpath) if "subagent" in set(cfg.get("checks", [])) else []

    problems = evaluate(final_text, entries, subs, cfg)
    if not problems:
        return 0

    sys.stderr.write(render(problems) + "\n")
    return 2


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        code = 0
    sys.exit(code)
