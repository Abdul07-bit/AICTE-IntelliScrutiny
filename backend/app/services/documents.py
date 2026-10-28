from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Application, Document, User
from app.services import audit, storage

DOC_TYPES = ["APPLICATION_FORM", "FIRE_SAFETY_CERTIFICATE", "STRUCTURAL_STABILITY_CERTIFICATE", "OCCUPANCY_CERTIFICATE",
             "ACCREDITATION_CERTIFICATE", "AFFIDAVIT", "LAND_DOCUMENT", "BUILDING_PLAN", "FACULTY_LIST",
             "INFRASTRUCTURE_PHOTO", "OTHER"]
EDITABLE_STATES = {"DRAFT", "RECTIFICATION_REQUESTED"}


def safe_filename(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", (name or "upload").split("/")[-1].split("\\")[-1]).strip()
    return name[:120] or "upload"


def ingest(db: Session, app: Application, user: User | None, filename: str, declared_ctype: str | None, data: bytes,
           declared_type: str | None, description: str | None = None, fixture_pages: list[str] | None = None,
           enforce_state: bool = True) -> Document:
    if enforce_state and app.status not in EDITABLE_STATES:
        raise AppError(409, "APPLICATION_LOCKED", f"Documents cannot be added while the application is {app.status}")
    if declared_type and declared_type not in DOC_TYPES:
        raise AppError(422, "INVALID_DOC_TYPE", "Unknown document type", {"allowed": DOC_TYPES})
    ctype = storage.validate_upload(data, declared_ctype, filename)
    scan = storage.malware_scan(data)
    digest = storage.sha256(data)
    key, encrypted = storage.put(data, f"applications/{app.id}", "." + ctype.split("/")[-1])
    version = 1 + len(db.scalars(select(Document.id).where(Document.application_id == app.id,
                                                          Document.declared_type == declared_type,
                                                          Document.filename == safe_filename(filename))).all())
    doc = Document(
        application_id=app.id, institution_id=app.institution_id, filename=safe_filename(filename),
        content_type=ctype, size_bytes=len(data), sha256=digest, storage_key=key, encrypted=encrypted,
        declared_type=declared_type, status="QUARANTINED" if scan.status == "INFECTED" else "UPLOADED",
        malware_scan_status=scan.status, malware_scan_engine=scan.engine, version=version,
        uploaded_by_id=user.id if user else None, is_synthetic=app.is_synthetic,
        file_metadata={"description": description, **({"fixture_pages": fixture_pages} if fixture_pages else {}),
                       "scan_detail": scan.detail},
    )
    db.add(doc)
    db.flush()
    audit.record(db, "document.uploaded", "document", doc.id, actor=user, application_id=app.id,
                 institution_id=app.institution_id,
                 details={"filename": doc.filename, "sha256": digest, "size": len(data), "content_type": ctype,
                          "declared_type": declared_type, "malware_scan": scan.status, "encrypted": encrypted})
    if scan.status == "INFECTED":
        audit.record(db, "document.quarantined", "document", doc.id, actor=user, application_id=app.id,
                     details={"engine": scan.engine, "detail": scan.detail})
    return doc
