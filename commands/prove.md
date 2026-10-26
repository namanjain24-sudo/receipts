---
description: Check whether the test backing your claim would pass even without your change
---

Run the inert-test check for this session.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/prove.py" --session "$CLAUDE_SESSION_ID" --cwd "$(pwd)" $ARGUMENTS
```

Read the result and act on it:

- **PROVEN** — the test fails at HEAD and passes with the change. Quote the line that proves it and you are done.
- **INERT** — the test is green with or without the change, so it proves nothing. Write a test that fails when the change is reverted, run it, and check again.
- **INCONCLUSIVE** — the test could not run at HEAD, usually a missing dependency in the throwaway worktree. Say so plainly; do not report it as a pass.

Pass `--cmd "<command>"` to check a specific test command instead of the last one that ran.
