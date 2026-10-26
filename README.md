# Receipts

**Don't tell me it's done. Show me the receipt.**

A Claude Code plugin that refuses to let a turn end on *"done"*, *"tests pass"*
or *"it works"* when nothing that actually ran supports it.

The proof is recorded by hooks, straight from real tool output. The model never
writes its own report card.

---

## The problem

Across 547 real agent jobs, *"done, tests pass"* was reported **265 times**.
Re-run in a clean sandbox, about **1 in 11 of those claims was false**.

The worse number: in **8 cases the test still passed after the production
change was reverted.** The tests ran. The tests were green. The tests were
measuring nothing.

SonarSource's 2026 survey: **96%** of developers don't fully trust AI-written
code — and only **48%** verify it before committing. The gap between those two
numbers is this plugin's job.

The model is not lying. It is reporting an impression of its own work, and an
impression is not evidence.

---

## What it does

Two hooks and a command:

- **`PostToolUse`** writes an append-only ledger of what every Bash, Edit and
  Write call actually did — including a pass/fail verdict parsed out of the
  real output.
- **`Stop`** reads the final message, finds completion claims, and refuses the
  stop when the ledger does not back them.
- **`/prove`** answers the question no other gate asks: *would this test have
  passed anyway?*

### Five ways "done" goes wrong

| | Shape | Caught by |
|---|---|---|
| 1 | The check **failed** and the summary didn't say so | reading the real output, not the model's account of it |
| 2 | The check is **stale** — code changed after it ran | ledger ordering: mutations and checks share one sequence |
| 3 | A **subagent** reported a failure the parent dropped | scanning subagent results in the transcript |
| 4 | **No check of that kind ran at all** | claim kind → required evidence family |
| 5 | The test is **inert** — green with or without the change | `/prove`: re-runs it at HEAD in a throwaway worktree |

**Shape 5 is the one everyone misses**, because from the inside it looks
identical to success. The tests ran, they were green, the agent reported
honestly. Only re-running them against code that *doesn't have the change*
tells them apart.

---

## What it looks like

```
Receipts: 1 claim in this message is not backed by what actually ran.

  [stale] you wrote: All tests pass now.
           but the last passing check was `pytest -q` (120 passed), and
           src/parser.py changed after it.
           -> run the project's test command and read the summary line,
              then say what it printed.

Either produce the check, or rewrite the claim to match what you actually
know. Saying it is unverified is a fine answer.
```

And the inert check:

```
$ /prove
Checking whether this test would pass without your change.
  command: python3 -m unittest -q
  against: HEAD, in a throwaway worktree

INERT -- the test passes at HEAD too, so it would be green with or without
your change, so it does not prove the change works
```

---

## Install

```bash
claude plugin marketplace add namanjain24-sudo/receipts
claude plugin install receipts@receipts
```

Requires `python3` on PATH. No dependencies — standard library only.

---

## How it works

Three things about Claude Code's hook system shape the design, and two of them
are not in the docs:

**The Stop hook can refuse.** Exit 2 with a reason on stderr blocks the stop
and hands the reason back to the model, which then has to act on it. It is the
one hook that inverts control. `stop_hook_active` guards against looping.

**A PostToolUse hook does not get an exit code.** The Bash payload carries only
`stdout`, `stderr`, `interrupted`, `isImage` and `noOutputExpected` — there is
no exit status anywhere in it. So verdicts are parsed from output, with a
per-framework parser for pytest, jest/vitest, go test, cargo, mocha and tsc,
plus a generic failure-line pass. **Anything unparseable is recorded as
`unknown`, and `unknown` is never treated as a pass.**

**Hooks run in parallel on the original output.** So Receipts only ever
appends to its own ledger; it never rewrites tool output, and cannot clobber
another plugin that does.

The inert check uses `git worktree add --detach` into a temp directory, links
`node_modules`/`.venv`/`target` rather than reinstalling them, runs the test
there and removes the worktree. **It never touches your working tree.**

---

## Configuration

`.claude/receipts.json` in your project, all fields optional:

```json
{
  "enabled": true,
  "locales": ["en"],
  "checks": ["failing", "stale", "subagent", "missing", "inert"]
}
```

Add `"hinglish"` to `locales` for romanised Hindi claim phrasing
(*"ho gaya"*, *"tests pass ho gaye"*). Drop a name from `checks` to disable
that rule.

---

## What it does *not* do

Stated plainly, because a verification tool that overstates itself is the
thing it is supposed to prevent:

- **It is not tamper-proof.** An agent with shell access could write to the
  ledger. The threat model is an optimistic agent, not an adversarial one.
- **It does not read your code.** It cannot tell a good test from a bad one —
  only whether a test discriminates, via `/prove`.
- **The inert check needs git and uncommitted work.** Already committed, no
  repo, or a worktree that can't install its deps → `INCONCLUSIVE`, never a
  false pass.
- **Claim detection is pattern-based**, so a sufficiently creative phrasing
  gets through. Hedges are deliberately exempt: *"tests should pass"* is not
  a claim.
- **It only sees families it knows.** An exotic test runner lands in
  `unknown`, which blocks rather than passes — noisier, never wrong in the
  dangerous direction.

---

## Tests

```bash
python3 tests/test_receipts.py
```

20 end-to-end cases. They drive `hooks/gate.py` and `hooks/record.py` as real
subprocesses over stdin — the same contract Claude Code uses — including two
that build actual git repositories, one with a discriminating test and one
with an inert test, and assert the checker tells them apart.

---

MIT
