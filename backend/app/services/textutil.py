from __future__ import annotations

import re
from datetime import date, datetime
from difflib import SequenceMatcher

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], start=1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})

_STOP = {"the", "of", "and", "for", "a", "an", "&"}


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    v = value.strip().rstrip(".,")
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    m = re.match(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s+(\d{4})", v)
    if m and m.group(2).lower() in _MONTHS:
        try:
            return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            return None
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", v)
    if m and m.group(1).lower() in _MONTHS:
        try:
            return date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
        except ValueError:
            return None
    return None


DATE_PATTERN = (
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s+\d{4}"
    r"|[A-Za-z]+\s+\d{1,2},\s+\d{4})"
)


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    n = name.lower().replace("&", " and ")
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return " ".join(t for t in n.split() if t not in _STOP)


def name_similarity(a: str | None, b: str | None) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb)
    return round(0.5 * jaccard + 0.5 * SequenceMatcher(None, na, nb).ratio(), 4)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)?", text.lower())


def text_similarity(a: str, b: str) -> float:
    """Shingle-based Jaccard similarity used for near-duplicate detection."""
    def shingles(t: str) -> set[str]:
        toks = tokenize(t)
        return {" ".join(toks[i:i + 3]) for i in range(max(1, len(toks) - 2))}

    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
