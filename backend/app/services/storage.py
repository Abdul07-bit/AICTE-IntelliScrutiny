"""File storage with content validation, malware-scan integration point, hashing and
optional encryption at rest."""
from __future__ import annotations

import hashlib
import io
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import AppError

_MAGIC = {
    "application/pdf": [b"%PDF-"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/jpeg": [b"\xff\xd8\xff"],
}
_EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!"


def sniff_content_type(data: bytes) -> str | None:
    for ctype, sigs in _MAGIC.items():
        if any(data.startswith(sig) for sig in sigs):
            return ctype
    return None


def validate_upload(data: bytes, declared_ctype: str | None, filename: str) -> str:
    s = get_settings()
    if not data:
        raise AppError(400, "EMPTY_FILE", "Uploaded file is empty")
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise AppError(413, "PAYLOAD_TOO_LARGE", f"File exceeds {s.max_upload_mb} MB limit")
    sniffed = sniff_content_type(data)
    if sniffed is None or sniffed not in s.allowed_upload_types:
        raise AppError(415, "UNSUPPORTED_TYPE", "Only PDF, PNG and JPEG files are accepted",
                       {"filename": filename})
    if declared_ctype and declared_ctype not in ("application/octet-stream", sniffed):
        raise AppError(415, "CONTENT_MISMATCH", "File content does not match its declared type",
                       {"declared": declared_ctype, "detected": sniffed})
    return sniffed


@dataclass
class ScanResult:
    status: str  # CLEAN | INFECTED | ERROR | SKIPPED
    engine: str
    detail: str | None = None


def malware_scan(data: bytes) -> ScanResult:
    """Integration point. Uses ClamAV (clamd INSTREAM) when CLAMAV_HOST is set; otherwise a
    built-in signature check that only detects the EICAR test pattern."""
    s = get_settings()
    if s.clamav_host:
        try:
            with socket.create_connection((s.clamav_host, s.clamav_port), timeout=10) as sock:
                sock.sendall(b"zINSTREAM\0")
                for i in range(0, len(data), 8192):
                    chunk = data[i:i + 8192]
                    sock.sendall(len(chunk).to_bytes(4, "big") + chunk)
                sock.sendall(b"\0\0\0\0")
                reply = sock.recv(4096).decode(errors="replace")
            if "FOUND" in reply:
                return ScanResult("INFECTED", "clamav", reply.strip())
            return ScanResult("CLEAN", "clamav")
        except OSError as exc:
            return ScanResult("ERROR", "clamav", f"scanner unreachable: {exc}")
    if _EICAR in data:
        return ScanResult("INFECTED", "builtin-signature", "EICAR test signature")
    return ScanResult("CLEAN", "builtin-signature (EICAR only — configure ClamAV for real scanning)")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fernet():
    key = get_settings().storage_encryption_key
    if not key:
        return None
    from cryptography.fernet import Fernet

    return Fernet(key.encode())


def put(data: bytes, prefix: str, suffix: str = "") -> tuple[str, bool]:
    s = get_settings()
    key = f"{prefix}/{uuid.uuid4().hex}{suffix}"
    path = s.storage_dir / key
    path.parent.mkdir(parents=True, exist_ok=True)
    f = _fernet()
    path.write_bytes(f.encrypt(data) if f else data)
    return key, f is not None


def get(key: str, encrypted: bool) -> bytes:
    path = (get_settings().storage_dir / key).resolve()
    if get_settings().storage_dir.resolve() not in path.parents:
        raise AppError(400, "BAD_STORAGE_KEY", "Invalid storage key")
    raw = path.read_bytes()
    if encrypted:
        f = _fernet()
        if f is None:
            raise AppError(500, "ENCRYPTION_KEY_MISSING", "Storage encryption key is not configured")
        return f.decrypt(raw)
    return raw


def delete(key: str) -> None:
    path = get_settings().storage_dir / key
    if path.exists():
        path.unlink()


def as_stream(data: bytes) -> io.BytesIO:
    return io.BytesIO(data)


def storage_path(key: str) -> Path:
    return get_settings().storage_dir / key
