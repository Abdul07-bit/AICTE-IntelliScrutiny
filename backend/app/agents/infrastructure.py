"""Infrastructure Evidence Assistant.

Performs only technically feasible preliminary checks on submitted images: perceptual-hash
duplication, image quality (resolution, exposure, sharpness) and capture-metadata presence.
Every output is a MACHINE_OBSERVATION — never a verified regulatory deficiency — and nothing
here infers room dimensions, physical compliance or legal sufficiency from a photograph.

Object/feature detection is exposed through `FeatureDetector`; no model is configured in this
prototype, so no object-level observations are produced (and none are claimed)."""
from __future__ import annotations

import io

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.common import add_finding, doc_ref
from app.models import Application, Document, ValidationRun
from app.services import storage

AGENT = "INFRASTRUCTURE"
DISCLAIMER = ("Machine observation only — not a verified deficiency. A photograph cannot establish dimensions, "
              "capacity or regulatory compliance.")


class FeatureDetector:
    """Integration point for an approved computer-vision model (e.g. lab equipment detection)."""

    name = "not-configured"

    def detect(self, image_bytes: bytes) -> list[dict]:
        return []


def average_hash(data: bytes) -> str:
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("L").resize((8, 8))
    px = list(img.getdata())
    avg = sum(px) / len(px)
    return f"{int(''.join('1' if p > avg else '0' for p in px), 2):016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def image_quality(data: bytes) -> dict:
    import cv2
    import numpy as np

    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    h, w = arr.shape
    return {"width": int(w), "height": int(h), "brightness": round(float(arr.mean()), 1),
            "sharpness": round(float(cv2.Laplacian(arr, cv2.CV_64F).var()), 1)}


def analyse(db: Session, app: Application, run: ValidationRun) -> dict:
    docs = db.scalars(select(Document).where(Document.application_id == app.id,
                                             Document.content_type.like("image/%"),
                                             Document.status == "PROCESSED")).all()
    detector = FeatureDetector()
    observations = []
    for d in docs:
        data = storage.get(d.storage_key, d.encrypted)
        d.perceptual_hash = average_hash(data)
        q = image_quality(data)
        exif = ((d.file_metadata or {}).get("extraction") or {}).get("exif") or {}
        meta = dict(d.file_metadata or {})
        meta["image_quality"] = q
        meta["feature_detector"] = detector.name
        meta["detected_features"] = detector.detect(data)
        d.file_metadata = meta
        issues = []
        if q["width"] < 640 or q["height"] < 480:
            issues.append(f"low resolution {q['width']}×{q['height']}")
        if q["brightness"] < 40 or q["brightness"] > 225:
            issues.append(f"poor exposure (mean brightness {q['brightness']})")
        if q["sharpness"] < 60:
            issues.append(f"low sharpness (Laplacian variance {q['sharpness']})")
        if not exif:
            issues.append("no capture metadata (date/device/location) present")
        if issues:
            observations.append(d.id)
            add_finding(db, app, run, agent=AGENT, category="MACHINE_OBSERVATION", code="IMAGE_QUALITY",
                        title=f"Image evidence limitations: {d.filename}", severity="LOW",
                        reason="; ".join(issues).capitalize() + ". " + DISCLAIMER, document_id=d.id,
                        observed="; ".join(issues), expected="clear, dated, geo-referenced photographs",
                        evidence=[doc_ref(d)], comparison={"quality": q, "exif_present": bool(exif)},
                        recommended_action="Request clearer or capture-dated photographs if this evidence is relied upon.")
    db.flush()
    for i, a in enumerate(docs):
        for b in docs[i + 1:]:
            dist = hamming(a.perceptual_hash, b.perceptual_hash)
            if dist <= 6:
                labels = [(x.file_metadata or {}).get("description") or x.filename for x in (a, b)]
                add_finding(db, app, run, agent=AGENT, category="MACHINE_OBSERVATION", code="IMAGE_NEAR_DUPLICATE",
                            title=f"Two infrastructure photos appear to show the same scene", severity="MEDIUM",
                            reason=f"Perceptual-hash distance {dist}/64 between '{labels[0]}' and '{labels[1]}'. If they "
                                   f"are presented as different facilities this needs clarification. " + DISCLAIMER,
                            document_id=b.id, observed=f"hash distance {dist}", expected="> 6 for distinct scenes",
                            evidence=[doc_ref(a), doc_ref(b)],
                            comparison={"method": "average-hash", "a": a.perceptual_hash, "b": b.perceptual_hash},
                            recommended_action="Ask the institution to clarify, or recommend a physical inspection.")
    add_finding(db, app, run, agent=AGENT, category="MACHINE_OBSERVATION", code="INFRASTRUCTURE_EVIDENCE_SUMMARY",
                title=f"{len(docs)} infrastructure image(s) analysed — compliance not established by images",
                severity="INFO" if docs else "LOW",
                reason=(f"Automated checks covered duplication, quality and capture metadata only. Object detection: "
                        f"{detector.name}. " + DISCLAIMER),
                observed=f"{len(docs)} image(s)", expected="reviewer assessment / inspection",
                evidence=[doc_ref(d) for d in docs],
                recommended_action="Reviewer to assess sufficiency; request additional evidence or recommend inspection.")
    return {"images": len(docs), "observations": len(observations)}
