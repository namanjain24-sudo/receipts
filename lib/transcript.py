"""Read the pieces of a Claude Code transcript the gate needs.

Claude Code writes one JSONL row per content block, so a single assistant
reply spans several rows sharing a requestId. The gate needs the text of the
final reply, and any subagent results in the turn -- a subagent that reports
failure into a parent that then claims success is one of the ways "done" goes
wrong without anyone lying on purpose.
"""
import json


def _rows(path):
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def final_message(path):
    """Text of the last assistant reply (all of its text blocks joined).

    Only a fallback: at Stop time the transcript has not caught up with the
    reply that triggered it, so the hook payload's `last_assistant_message`
    is the one to trust.
    """
    rows = _rows(path)
    last_req, parts = None, []
    for d in rows:
        if d.get("type") != "assistant":
            continue
        msg = d.get("message") or {}
        req = d.get("requestId") or msg.get("id")
        content = msg.get("content")
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = content
        else:
            continue
        texts = [b.get("text", "") for b in blocks
                 if isinstance(b, dict) and b.get("type") == "text"]
        if not any(t.strip() for t in texts):
            continue
        if req != last_req:
            last_req, parts = req, []
        parts.extend(texts)
    return "\n".join(p for p in parts if p).strip()


def subagent_results(path):
    """Text returned by Agent calls **in the current turn**.

    Scoped to everything after the last human message: a subagent failure from
    three turns ago has already been dealt with, and blocking on it forever
    would make the gate impossible to satisfy.
    """
    rows = _rows(path)

    start = 0
    for i, d in enumerate(rows):
        if d.get("type") != "user":
            continue
        content = (d.get("message") or {}).get("content")
        # a tool result is also a "user" row; a real human turn is plain text
        if isinstance(content, str) or (
                isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "text" for b in content)):
            if d.get("toolUseResult") is None:
                start = i

    agent_ids, out = set(), []
    for d in rows[start:]:
        content = (d.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "Agent":
                agent_ids.add(b.get("id"))
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in agent_ids:
                c = b.get("content")
                if isinstance(c, str):
                    out.append(c)
                elif isinstance(c, list):
                    out.extend(x.get("text", "") for x in c
                               if isinstance(x, dict) and x.get("type") == "text")
    return [t for t in out if t and t.strip()]
