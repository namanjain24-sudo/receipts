"""The append-only record of what actually happened in a session.

Written only by the PostToolUse hook, from real tool output. The model never
writes to it, which is the whole point: a claim is checked against machine
record, not against the model's recollection of its own work.
"""
import json, os, time

HOME = os.environ.get("RECEIPTS_HOME") or os.path.join(
    os.path.expanduser("~"), ".claude", "receipts")


def path_for(session_id):
    safe = "".join(c for c in (session_id or "nosession") if c.isalnum() or c in "-_")
    return os.path.join(HOME, "%s.jsonl" % (safe or "nosession"))


def append(session_id, entry):
    p = path_for(session_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    entry.setdefault("ts", time.time())
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return p


def read(session_id):
    p = path_for(session_id)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            e["seq"] = i
            out.append(e)
    return out


def last_mutation(entries):
    muts = [e for e in entries if e.get("kind") == "mutation"]
    return muts[-1] if muts else None


def checks_for(entries, families):
    """Every check whose family is in `families`, oldest first."""
    return [e for e in entries
            if e.get("kind") == "check" and e.get("family") in families]


def prune(days=7):
    """Ledgers are per-session and small, but they should not accumulate."""
    if not os.path.isdir(HOME):
        return 0
    cutoff, n = time.time() - days * 86400, 0
    for name in os.listdir(HOME):
        p = os.path.join(HOME, name)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p); n += 1
        except OSError:
            pass
    return n
