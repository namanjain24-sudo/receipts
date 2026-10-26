#!/usr/bin/env python3
"""SessionStart: drop ledgers from sessions that are long over.

One small file per session is cheap, but nothing was deleting them. Keeps a
week by default; RECEIPTS_KEEP_DAYS overrides it.
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import ledger  # noqa: E402

if __name__ == "__main__":
    try:
        ledger.prune(days=int(os.environ.get("RECEIPTS_KEEP_DAYS", "7")))
    except Exception:
        pass
    sys.exit(0)
