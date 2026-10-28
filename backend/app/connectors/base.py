"""Extensible verification-connector interface.

Rules every connector follows:
  * never scrape restricted systems or bypass authentication;
  * never fabricate issuer responses — MOCK connectors answer only from labelled synthetic
    registries stored in their configuration, and every response is flagged `is_simulated`;
  * an unreachable source is SOURCE_UNAVAILABLE, never evidence of forgery.
"""
from __future__ import annotations

import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

from app.db.types import utcnow

STATUSES = ["VERIFIED", "MISMATCH_DETECTED", "EXPIRED", "PENDING", "SOURCE_UNAVAILABLE",
            "MANUAL_REVIEW_REQUIRED", "NOT_APPLICABLE"]


class SourceUnavailable(Exception):
    """The configured source could not be reached or did not answer in time."""


@dataclass
class VerificationQuery:
    document_id: str | None
    doc_type: str | None
    fields: dict[str, str | None]
    qr_payloads: list[dict] = field(default_factory=list)
    reference_date: date | None = None


@dataclass
class ConnectorResponse:
    status: str
    summary: str
    response_meta: dict = field(default_factory=dict)
    mismatches: list[dict] = field(default_factory=list)
    is_simulated: bool = True


def new_request_ref(prefix: str = "VR") -> str:
    return f"{prefix}-{utcnow():%Y%m%d}-{secrets.token_hex(3).upper()}"


class VerificationConnector(ABC):
    kind: str = "ISSUER_API"

    def __init__(self, key: str, config: dict, mode: str = "MOCK"):
        self.key, self.config, self.mode = key, config or {}, mode

    def applies_to(self, query: VerificationQuery, doc_types: list[str]) -> bool:
        return not doc_types or query.doc_type in doc_types

    @abstractmethod
    def verify(self, query: VerificationQuery) -> ConnectorResponse: ...

    def health(self) -> tuple[str, str | None]:
        return "HEALTHY", None
