"""Find completion claims in the model's final message.

A gate that fires on every "done" is a gate people turn off in a day, so the
bar here is deliberately high: the sentence has to assert that something is
already true. Hedges ("should work"), conditionals ("once the tests pass"),
questions, and quoted or code-block text are all left alone.
"""
import re

#: claim kind -> patterns that assert it is already true
PATTERNS_EN = {
    "tests": [
        r"\btests?\s+(are\s+)?(now\s+)?(all\s+)?(passing|pass|green)\b",
        r"\ball\s+(the\s+)?tests?\s+pass",
        r"\b(test\s+suite|ci)\s+is\s+green\b",
        r"\b\d+\s*/\s*\d+\s+tests?\s+passing\b",
        r"\bsuite\s+(is\s+)?green\b",
    ],
    "builds": [
        r"\b(the\s+)?build\s+(is\s+)?(now\s+)?(succeed(s|ed)|passing|green|clean)\b",
        r"\bcompiles?\s+(now\s+)?(cleanly|without\s+errors?)\b",
        r"\bbuilds?\s+successfully\b",
    ],
    "lints": [
        r"\b(lint|linter|typecheck|type\s*check|mypy|tsc)\s+(is\s+)?(now\s+)?(clean|passing|green)\b",
        r"\bno\s+(more\s+)?(type|lint)\s+errors?\b",
    ],
    "deployed": [
        r"\b(it'?s|is|has\s+been)\s+(now\s+)?(deployed|live|shipped|in\s+production)\b",
        r"\b(deployed|shipped)\s+(it\s+)?(to|successfully)\b",
    ],
    "merged": [
        r"\b(i'?ve\s+)?(merged|pushed|committed)\s+(it|the\s+\w+|changes|this)\b",
        r"\b(the\s+)?(pr|branch)\s+(is\s+)?(now\s+)?merged\b",
    ],
    "works": [
        r"\b(it|this|that)\s+(now\s+)?works\s+(now|correctly|as\s+expected)?\b",
        r"\bworking\s+(now|correctly|as\s+expected)\b",
        r"\bverified\s+(that\s+)?it\s+works\b",
        r"\bconfirmed\s+working\b",
    ],
    "done": [
        r"\b(it'?s|this\s+is|that'?s|all)\s+(now\s+)?(done|complete|fixed|resolved|ready)\b",
        r"\b(i'?ve\s+)?(fixed|resolved|completed)\s+(it|the\s+(bug|issue|problem|error))\b",
        r"\beverything\s+(is\s+)?(now\s+)?(working|done|fixed)\b",
        r"\ball\s+set\b",
    ],
}

#: romanised Hindi/Hinglish, common in mixed-language sessions
PATTERNS_HINGLISH = {
    "tests":  [r"\btests?\s+pass\s+ho\s+ga?ye?\b", r"\bsare?\s+tests?\s+pass\b"],
    "builds": [r"\bbuild\s+(ho\s+gaya|clean\s+hai|pass\s+ho\s+gaya)\b"],
    "deployed": [r"\b(deploy|live)\s+ho\s+gaya\b"],
    "merged": [r"\b(push|merge|commit)\s+kar\s+diya\b"],
    "works":  [r"\b(ab\s+)?(kaam\s+kar\s+raha|chal\s+raha)\s+hai\b", r"\bsahi\s+chal\s+raha\s+hai\b"],
    "done":   [r"\b(ho\s+gaya|ban\s+gaya|fix\s+ho\s+gaya|theek\s+ho\s+gaya)\b",
               r"\bsab\s+(ho\s+gaya|theek\s+hai)\b"],
}

#: if one of these governs the sentence, it is not an assertion
HEDGES = [
    r"\bshould\b", r"\bwould\b", r"\bprobably\b", r"\blikely\b", r"\bexpect(ed)?\s+to\b",
    r"\bonce\b", r"\bafter\s+you\b", r"\bif\s+", r"\buntil\b", r"\bwhen\s+you\b",
    r"\bnot\s+(yet\s+)?", r"\bdoes\s*n[o']t\b", r"\bcan'?t\b", r"\bfail", r"\bbut\s+",
    r"\bneed\s+to\b", r"\bstill\b", r"\blet\s+me\b", r"\bi'?ll\b", r"\bgoing\s+to\b",
    r"\bplease\s+", r"\bcould\s+not\b", r"\bunable\b", r"\btry\b", r"\bassum",
    r"\bchahiye\b", r"\bhoga\b", r"\bkarna\s+(hai|padega)\b", r"\bnahi\b",
]


def _strip_noise(text):
    """Remove fenced code, inline code and block quotes -- claims inside those
    are usually the model quoting a log, not asserting anything."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub(r"^\s*>.*$", " ", text, flags=re.M)
    return text


def _sentences(text):
    for raw in re.split(r"(?<=[.!?\n])\s+", text):
        s = raw.strip(" \t-*#|")
        if s:
            yield s


def find(text, locales=("en",)):
    """Return [(kind, sentence)] for every completion claim asserted in `text`."""
    table = dict(PATTERNS_EN)
    if "hinglish" in locales:
        for k, v in PATTERNS_HINGLISH.items():
            table[k] = table.get(k, []) + v
    if "en" not in locales:
        table = {k: v for k, v in table.items() if "hinglish" in locales}

    out = []
    for sent in _sentences(_strip_noise(text)):
        if sent.endswith("?"):
            continue
        if any(re.search(h, sent, re.I) for h in HEDGES):
            continue
        for kind, pats in table.items():
            if any(re.search(p, sent, re.I) for p in pats):
                out.append((kind, sent[:200]))
                break
    return out
