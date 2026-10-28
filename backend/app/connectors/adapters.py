"""Connector adapters. MOCK adapters are deterministic and answer only from the labelled
synthetic registry in their configuration."""
from __future__ import annotations

import base64
import json
import time

from app.connectors.base import (
    ConnectorResponse,
    SourceUnavailable,
    VerificationConnector,
    VerificationQuery,
)
from app.services.textutil import name_similarity, parse_date


def canonical_payload(payload: dict) -> bytes:
    body = {k: v for k, v in payload.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


class MockIssuerApiConnector(VerificationConnector):
    """Simulates an official issuer's certificate-lookup API using a synthetic registry."""

    kind = "ISSUER_API"

    def _call(self) -> None:
        latency = float(self.config.get("simulated_latency_ms", 40)) / 1000
        time.sleep(min(latency, 2.0))
        if self.config.get("simulate_outage"):
            raise SourceUnavailable(self.config.get("outage_message", "Issuer endpoint did not respond (simulated outage)"))

    def health(self):
        if self.config.get("simulate_outage"):
            return "DOWN", "Simulated outage configured"
        return "HEALTHY", None

    def verify(self, q: VerificationQuery) -> ConnectorResponse:
        self._call()
        cert_no = (q.fields.get("certificate_number") or "").upper()
        if not cert_no:
            return ConnectorResponse("MANUAL_REVIEW_REQUIRED",
                                     "Certificate number could not be extracted; issuer lookup not possible")
        record = (self.config.get("records") or {}).get(cert_no)
        meta = {"issuer": self.config.get("issuer_name"), "endpoint": self.config.get("endpoint", "mock://"),
                "lookup_key": cert_no, "registry": "SYNTHETIC — labelled demonstration records"}
        if record is None:
            return ConnectorResponse("MANUAL_REVIEW_REQUIRED",
                                     "No matching record returned by the issuer source. This does not establish "
                                     "that the document is invalid; manual confirmation is required.", meta)
        meta["issuer_record"] = record
        mismatches = []
        holder = q.fields.get("institution_name")
        if holder and name_similarity(holder, record.get("holder")) < 0.97:
            mismatches.append({"field": "institution_name", "document_value": holder, "issuer_value": record.get("holder")})
        for f in ("issue_date", "valid_until"):
            dv, iv = parse_date(q.fields.get(f)), parse_date(record.get(f))
            if dv and iv and dv != iv:
                mismatches.append({"field": f, "document_value": dv.isoformat(), "issuer_value": iv.isoformat()})
        if record.get("status") == "REVOKED":
            return ConnectorResponse("MISMATCH_DETECTED", "Issuer reports the certificate as revoked", meta, mismatches)
        if mismatches:
            return ConnectorResponse("MISMATCH_DETECTED",
                                     f"Issuer record differs from the submitted document on {', '.join(m['field'] for m in mismatches)}",
                                     meta, mismatches)
        iv = parse_date(record.get("valid_until"))
        if iv and q.reference_date and iv < q.reference_date:
            return ConnectorResponse("EXPIRED", f"Issuer confirms the certificate, valid until {iv.isoformat()}, "
                                                f"which is before the reference date {q.reference_date.isoformat()}", meta)
        return ConnectorResponse("VERIFIED", "Issuer record matches the submitted document", meta)


class QrResolverConnector(VerificationConnector):
    """Parses QR payloads embedded in a document and checks them against extracted fields."""

    kind = "QR"

    def applies_to(self, q, doc_types):
        return bool(q.qr_payloads)

    def verify(self, q: VerificationQuery) -> ConnectorResponse:
        if not q.qr_payloads:
            return ConnectorResponse("NOT_APPLICABLE", "No QR code found in the document")
        p = next((x["json"] for x in q.qr_payloads if x.get("json")), None)
        if p is None:
            return ConnectorResponse("MANUAL_REVIEW_REQUIRED", "QR code present but its payload is not a recognised format",
                                     {"raw": q.qr_payloads[0].get("raw", "")[:200]})
        known = self.config.get("known_issuers", {})
        meta = {"payload": {k: v for k, v in p.items() if k != "sig"}, "issuer_known": p.get("iss") in known}
        if p.get("iss") not in known:
            return ConnectorResponse("MANUAL_REVIEW_REQUIRED", f"QR issuer '{p.get('iss')}' is not a configured issuer", meta)
        mismatches = []
        for qf, df in (("cert", "certificate_number"), ("holder", "institution_name"), ("valid_until", "valid_until")):
            dv = q.fields.get(df)
            if dv is None or p.get(qf) is None:
                continue
            same = (name_similarity(dv, p[qf]) >= 0.97) if df == "institution_name" else (
                (parse_date(dv) == parse_date(p[qf])) if df == "valid_until" else dv.upper() == str(p[qf]).upper())
            if not same:
                mismatches.append({"field": df, "document_value": dv, "issuer_value": p[qf], "source": "qr_payload"})
        if mismatches:
            return ConnectorResponse("MISMATCH_DETECTED", "QR payload differs from the printed document fields", meta, mismatches)
        return ConnectorResponse("VERIFIED", "QR payload matches the printed document fields", meta)


class DigitalSignatureConnector(VerificationConnector):
    """Verifies Ed25519 signatures on QR payloads against registered issuer public keys."""

    kind = "DIGITAL_SIGNATURE"

    def applies_to(self, q, doc_types):
        return any(x.get("json") and x["json"].get("sig") for x in q.qr_payloads)

    def verify(self, q: VerificationQuery) -> ConnectorResponse:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        p = next((x["json"] for x in q.qr_payloads if x.get("json") and x["json"].get("sig")), None)
        if p is None:
            return ConnectorResponse("NOT_APPLICABLE", "No signed payload found")
        key_b64 = (self.config.get("issuer_public_keys") or {}).get(p.get("iss"))
        meta = {"issuer": p.get("iss"), "algorithm": "Ed25519", "key_registered": bool(key_b64)}
        if not key_b64:
            return ConnectorResponse("MANUAL_REVIEW_REQUIRED", "No registered public key for this issuer", meta)
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64)).verify(
                base64.b64decode(p["sig"]), canonical_payload(p))
            meta["signature_valid"] = True
            return ConnectorResponse("VERIFIED", "Signature is valid for the registered issuer key", meta)
        except (InvalidSignature, ValueError):
            meta["signature_valid"] = False
            return ConnectorResponse(
                "MANUAL_REVIEW_REQUIRED",
                "Signature did not validate against the registered issuer key. This can result from re-issuance, "
                "key rotation or alteration; it is not by itself proof of forgery. Manual issuer confirmation required.",
                meta)


class DigiLockerConnector(VerificationConnector):
    """Placeholder for a DigiLocker-compatible integration. Disabled until official authorization,
    credentials and an approved integration agreement exist. Makes no network calls."""

    kind = "DIGILOCKER"

    def health(self):
        return "NOT_CONFIGURED", "Requires official authorization and credentials"

    def verify(self, q: VerificationQuery) -> ConnectorResponse:
        return ConnectorResponse("SOURCE_UNAVAILABLE", "DigiLocker-compatible connector is not authorized/configured",
                                 {"authorized": False}, is_simulated=False)


class ManualConfirmationConnector(VerificationConnector):
    kind = "MANUAL"

    def applies_to(self, q, doc_types):
        return False  # only used explicitly by reviewers

    def verify(self, q: VerificationQuery) -> ConnectorResponse:
        return ConnectorResponse("PENDING", "Queued for manual issuer confirmation by a verification officer",
                                 {"queue": "manual-confirmation"}, is_simulated=False)


ADAPTERS: dict[str, type[VerificationConnector]] = {
    "mock_issuer_api": MockIssuerApiConnector,
    "qr_resolver": QrResolverConnector,
    "digital_signature": DigitalSignatureConnector,
    "digilocker": DigiLockerConnector,
    "manual": ManualConfirmationConnector,
}
