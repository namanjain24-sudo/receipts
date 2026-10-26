#!/usr/bin/env python3
"""`/prove` -- run the inert-test check and write the result into the ledger.

Usage:
    prove.py --session <id> [--cmd "<test command>"] [--cwd <dir>] [--timeout 600]

With no --cmd it reuses the last test command the recorder saw, which is the
command that produced the green run being claimed.
"""
import argparse, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import inert, ledger  # noqa: E402


def newest_session():
    """Fall back to the most recent ledger when the host gave us no session id."""
    try:
        names = [n for n in os.listdir(ledger.HOME) if n.endswith(".jsonl")]
    except OSError:
        return None
    if not names:
        return None
    names.sort(key=lambda n: os.path.getmtime(os.path.join(ledger.HOME, n)))
    return names[-1][:-len(".jsonl")]


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--session", default="")
    ap.add_argument("--cmd", default=None)
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()

    session = a.session or newest_session()
    if not session:
        print("No Receipts ledger found for this session. The plugin's PostToolUse "
              "hook has not recorded anything yet.")
        return 1
    entries = ledger.read(session)
    cmd, cwd = a.cmd, a.cwd
    if not cmd:
        tests = [e for e in entries if e.get("kind") == "check" and e.get("family") == "test"]
        if not tests:
            print("No test command has run in this session yet, so there is nothing to "
                  "prove. Run the tests first, then /prove.")
            return 1
        cmd = tests[-1]["cmd"]
        cwd = tests[-1].get("cwd") or cwd

    print("Checking whether this test would pass without your change.")
    print("  command: %s" % cmd)
    print("  against: HEAD, in a throwaway worktree\n")

    status, detail = inert.check(cmd, cwd, timeout=a.timeout)
    ledger.append(session, {"kind": "inert", "cmd": cmd, "status": status,
                              "detail": detail[:600], "cwd": cwd})

    mark = {"PROVEN": "PROVEN", "INERT": "INERT", "INCONCLUSIVE": "INCONCLUSIVE"}[status]
    print("%s -- %s" % (mark, detail))
    if status == "INERT":
        print("\nWrite a test that fails at HEAD. The smallest one that would break "
              "if your change were reverted is the one worth keeping.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
