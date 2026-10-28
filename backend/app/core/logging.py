"""Logging with sensitive-data redaction."""
from __future__ import annotations

import logging
import re

_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "[id-12]"),  # Aadhaar-like
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[pan]"),
    (re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)"), "[phone]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._-]+"), r"\1[token]"),
    (re.compile(r"(?i)(password|secret|api[_-]?key|token)(\"?\s*[:=]\s*\"?)[^\s\",]+"), r"\1\2[redacted]"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
