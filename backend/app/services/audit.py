"""Append-only, hash-chained audit trail."""
from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.types import utcnow
from app.models import AuditEvent, User

# Populated by request dependencies so services can attribute events without plumbing.
request_ctx: ContextVar[dict] = ContextVar("request_ctx", default={})


def _digest(prev_hash: str | None, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(((prev_hash or "") + body).encode()).hexdigest()


def record(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    *,
    actor: User | None = None,
    application_id: str | None = None,
    institution_id: str | None = None,
    details: dict[str, Any] | None = None,
    source_refs: list | None = None,
) -> AuditEvent:
    ctx = request_ctx.get()
    last = db.execute(select(AuditEvent.seq, AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1)).first()
    seq = (last[0] + 1) if last else 1
    prev_hash = last[1] if last else None
    ts = utcnow()
    payload = {
        "seq": seq,
        "ts": ts.isoformat(),
        "actor_id": actor.id if actor else ctx.get("actor_id"),
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "application_id": application_id,
        "details": details or {},
    }
    ev = AuditEvent(
        seq=seq,
        ts=ts,
        actor_id=payload["actor_id"],
        actor_email=actor.email if actor else ctx.get("actor_email", "system"),
        actor_role=actor.role if actor else ctx.get("actor_role", "SYSTEM"),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        application_id=application_id,
        institution_id=institution_id,
        request_id=ctx.get("request_id"),
        ip=ctx.get("ip"),
        details=details or {},
        source_refs=source_refs or [],
        prev_hash=prev_hash,
        hash=_digest(prev_hash, payload),
    )
    db.add(ev)
    db.flush()
    return ev


def verify_chain(db: Session, limit: int = 5000) -> dict:
    """Recompute hashes to detect tampering with stored audit events."""
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.seq).limit(limit)).all()
    prev = None
    for ev in events:
        payload = {
            "seq": ev.seq,
            "ts": ev.ts.isoformat(),
            "actor_id": ev.actor_id,
            "action": ev.action,
            "entity_type": ev.entity_type,
            "entity_id": ev.entity_id,
            "application_id": ev.application_id,
            "details": ev.details or {},
        }
        if ev.prev_hash != prev or _digest(prev, payload) != ev.hash:
            return {"valid": False, "broken_at_seq": ev.seq, "checked": len(events)}
        prev = ev.hash
    total = db.scalar(select(func.count()).select_from(AuditEvent))
    return {"valid": True, "checked": len(events), "total": total}
