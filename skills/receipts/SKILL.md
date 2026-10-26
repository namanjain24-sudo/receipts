---
name: receipts
description: Use when reporting that work is finished - that tests pass, a build is green, something is deployed, fixed, or working. Also use when a Receipts gate has blocked the end of a turn, or when deciding what check would actually prove a change works. Covers evidence for completion claims, stale test runs, tests that pass whether or not the change is present, and failures buried in subagent output.
---

# Receipts

Say what you ran. Quote what it printed. That is the whole discipline.

## Why this exists

Across 547 real agent jobs, "done, tests pass" was reported 265 times. Re-run
in a fresh sandbox, roughly **1 in 11 of those claims was false** — and in 8
cases the test passed even when the production change was reverted, so the
test was proving nothing in the first place. The model is not lying. It is
reporting an impression of its own work, and an impression is not a receipt.

## The rule

Before writing *done*, *fixed*, *tests pass*, *deployed*, or *it works*, there
must be a command in this turn whose output says so, run **after** the last
edit.

A claim is backed when all four hold:

1. **It ran.** A real command, not a plan to run one.
2. **It is fresh.** No file changed after it.
3. **It discriminates.** It would fail if the change were reverted.
4. **Nothing is hidden.** No subagent reported a failure you left out.

## When the gate blocks you

The Stop hook returns one of five reasons. Each has one correct response.

| Reason | What it means | Do this |
|---|---|---|
| `failing` | The last check reported a failure | Read the failure. Fix it, or say it still fails. |
| `stale` | Code changed after the last green run | Re-run the check. |
| `inert` | The test passes at HEAD too | Write a test that fails when the change is reverted. |
| `subagent` | A subagent reported a failure you did not mention | Surface it. |
| `missing` | No check of that kind ran at all | Run the smallest one that would fail if you were wrong. |

**Do not** try to get past the gate by rephrasing. Weakening "tests pass" to
"tests should pass" clears the hedge filter but tells the user less than the
truth would. If you cannot verify it, the honest sentence is:

> I changed X. I did not run the tests, so I do not know whether they pass.

That sentence always passes the gate, because it claims nothing.

## Picking the check

The right check is the **smallest command that would fail if your claim were
false.** Not the full suite — the one assertion that discriminates.

- Changed a parser? Feed it the input that used to break.
- Fixed a 500? `curl` the route and read the status line.
- Claiming it deployed? Hit the deployed URL, not localhost.

Then run `/prove` to confirm the test is not inert.

## What counts as a receipt

Quote the line that decided it:

```
$ pytest tests/test_parser.py -q
1 failed, 23 passed in 0.84s
FAILED tests/test_parser.py::test_nested — AssertionError: expected 3, got 2
```

Not `I ran the tests and they pass`. The first can be checked by the reader.
The second asks them to trust you.

## What the gate ignores

Tuned against 1,356 real turns, so it stays quiet about:

- **hedges** — *"tests should pass"*, *"once you install deps"*
- **instructions to the reader** — *"bata dena kaisa chal raha hai"*
- **breakage** — *"nginx crash ho gaya"* is not a completion
- **other people's work** — *"PR merged by someone"*
- **read-only turns** — a vague *"done"* in a turn that edited nothing
- **docs and housekeeping** — a README edit does not make a test run stale

## Configuration

`.claude/receipts.json`, all fields optional:

```json
{
  "enabled": true,
  "locales": ["en"],
  "checks": ["failing", "stale", "subagent", "missing", "inert"]
}
```

Add `"hinglish"` to `locales` for romanised Hindi claim phrasing. Drop a name
from `checks` to turn that one rule off.
