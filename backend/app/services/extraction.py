"""Document intelligence pipeline: text-layer extraction, OCR, QR decoding, classification and
schema-constrained field extraction.

Extraction methods recorded per page:
  pdf-text-layer     text read from the PDF's embedded text layer (pypdf)
  tesseract-ocr      OCR via Tesseract (when installed)
  synthetic-fixture  labelled fixture text supplied for demo speed — never presented as OCR
  none               no text could be obtained (e.g. OCR engine unavailable)
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ValidationError, create_model

from app.core.config import get_settings
from app.services.textutil import DATE_PATTERN, parse_date

log = logging.getLogger("intelliscrutiny.extraction")
PIPELINE_VERSION = "extract-2026.10.1"


# ---------------------------------------------------------------- data structures
@dataclass
class Line:
    text: str
    bbox: list[float]  # normalised [x0, y0, x1, y1], origin top-left


@dataclass
class Page:
    page_no: int
    text: str
    lines: list[Line]
    width: float | None
    height: float | None
    method: str
    confidence: float | None
    images: list[bytes] = field(default_factory=list)


@dataclass
class FieldValue:
    name: str
    value: str | None
    normalized: str | None
    confidence: float
    page_no: int | None
    bbox: list[float] | None
    method: str


@dataclass
class ExtractionResult:
    pages: list[Page]
    metadata: dict
    doc_type: str
    classification_confidence: float
    classification_scores: dict
    fields: list[FieldValue]
    schema_errors: list[str]
    qr_payloads: list[dict]
    method: str
    confidence: float
    warnings: list[str]


# ---------------------------------------------------------------- OCR availability
_tesseract_ok: bool | None = None


def tesseract_available() -> bool:
    global _tesseract_ok
    if _tesseract_ok is None:
        try:
            import pytesseract

            if get_settings().tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = get_settings().tesseract_cmd
            pytesseract.get_tesseract_version()
            _tesseract_ok = True
        except Exception:
            _tesseract_ok = False
    return _tesseract_ok and get_settings().ocr_enabled


def _ocr_image(img_bytes: bytes) -> tuple[str, list[Line], float]:
    import pytesseract
    from PIL import Image

    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    rows: dict[tuple, list[int]] = {}
    for i, word in enumerate(data["text"]):
        if word.strip():
            rows.setdefault((data["block_num"][i], data["par_num"][i], data["line_num"][i]), []).append(i)
    lines, confs = [], []
    for idxs in rows.values():
        text = " ".join(data["text"][i] for i in idxs)
        x0 = min(data["left"][i] for i in idxs); y0 = min(data["top"][i] for i in idxs)
        x1 = max(data["left"][i] + data["width"][i] for i in idxs)
        y1 = max(data["top"][i] + data["height"][i] for i in idxs)
        lines.append(Line(text, [x0 / w, y0 / h, x1 / w, y1 / h]))
        confs += [float(data["conf"][i]) for i in idxs if float(data["conf"][i]) >= 0]
    lines.sort(key=lambda ln: (round(ln.bbox[1], 3), ln.bbox[0]))
    conf = (sum(confs) / len(confs) / 100) if confs else 0.0
    return "\n".join(ln.text for ln in lines), lines, round(conf, 3)


# ---------------------------------------------------------------- PDF handling
def _pdf_date(value: Any) -> str | None:
    if not value:
        return None
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?", str(value))
    if not m:
        return str(value)
    parts = [int(p) if p else 0 for p in m.groups()]
    try:
        return datetime(*parts).isoformat()
    except ValueError:
        return str(value)


def _extract_pdf(data: bytes) -> tuple[list[Page], dict]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    info = reader.metadata or {}
    meta = {
        "producer": str(info.get("/Producer", "") or "") or None,
        "creator": str(info.get("/Creator", "") or "") or None,
        "author": str(info.get("/Author", "") or "") or None,
        "title": str(info.get("/Title", "") or "") or None,
        "creation_date": _pdf_date(info.get("/CreationDate")),
        "modification_date": _pdf_date(info.get("/ModDate")),
        "incremental_updates": max(0, data.count(b"%%EOF") - 1),
        "encrypted": reader.is_encrypted,
    }
    pages: list[Page] = []
    for idx, pg in enumerate(reader.pages, start=1):
        width, height = float(pg.mediabox.width), float(pg.mediabox.height)
        frags: list[tuple[str, float, float, float]] = []

        def visitor(text, cm, tm, _font, size):
            if not text or not text.strip():
                return
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            fs = abs(size * (tm[3] or 1) * (cm[3] or 1)) or 10
            frags.append((text.replace("\n", " "), x, y, fs))

        raw_text = pg.extract_text(visitor_text=visitor) or ""
        lines: list[Line] = []
        if frags:
            rows: dict[int, list] = {}
            for t, x, y, fs in frags:
                rows.setdefault(round(y / 2), []).append((t, x, y, fs))
            for key in sorted(rows, reverse=True):
                parts = sorted(rows[key], key=lambda f: f[1])
                text = re.sub(r"\s+", " ", " ".join(p[0].strip() for p in parts)).strip()
                if not text:
                    continue
                x0 = min(p[1] for p in parts)
                fs = max(p[3] for p in parts)
                x1 = min(width, max(p[1] + len(p[0]) * p[3] * 0.5 for p in parts))
                y = parts[0][2]
                lines.append(Line(text, [round(x0 / width, 4), round(max(0, height - y - fs) / height, 4),
                                         round(x1 / width, 4), round(min(height, height - y + fs * 0.3) / height, 4)]))
        text = "\n".join(ln.text for ln in lines) if lines else raw_text.strip()
        images = []
        try:
            for im in pg.images:
                buf = io.BytesIO()
                im.image.save(buf, "PNG")
                images.append(buf.getvalue())
        except Exception as exc:  # pragma: no cover - malformed images
            log.warning("image extraction failed on page %s: %s", idx, exc)
        pages.append(Page(idx, text, lines, width, height, "pdf-text-layer" if text.strip() else "none",
                          0.97 if text.strip() else None, images))
    return pages, meta


def _image_page(data: bytes) -> tuple[list[Page], dict]:
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    meta = {"format": img.format, "width": img.width, "height": img.height, "mode": img.mode}
    try:
        exif = img.getexif()
        meta["exif"] = {str(k): str(v)[:80] for k, v in exif.items()} if exif else {}
    except Exception:
        meta["exif"] = {}
    return [Page(1, "", [], float(img.width), float(img.height), "none", None, [data])], meta


def _apply_ocr(pages: list[Page], warnings: list[str]) -> None:
    """OCR pages that lack a usable text layer."""
    for page in pages:
        if len(page.text.strip()) >= 20 or not page.images:
            continue
        if not tesseract_available():
            warnings.append(f"Page {page.page_no}: no text layer and OCR engine is unavailable")
            continue
        texts, lines, confs = [], [], []
        for img in page.images:
            try:
                t, ls, c = _ocr_image(img)
                if t.strip():
                    texts.append(t); lines.extend(ls); confs.append(c)
            except Exception as exc:
                warnings.append(f"Page {page.page_no}: OCR failed ({type(exc).__name__})")
        if texts:
            page.text, page.lines = "\n".join(texts), lines
            page.method, page.confidence = "tesseract-ocr", round(sum(confs) / len(confs), 3)


def decode_qr_codes(images: list[bytes]) -> list[dict]:
    """Decode QR codes; payloads are parsed as JSON when possible (signed issuer payloads)."""
    out: list[dict] = []
    try:
        import cv2
        import numpy as np
    except ImportError:  # pragma: no cover
        return out
    detector = cv2.QRCodeDetector()
    for raw in images:
        arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
        if arr is None:
            try:
                from PIL import Image

                pil = Image.open(io.BytesIO(raw)).convert("L")
                arr = np.array(pil)
            except Exception:
                continue
        if arr.shape[0] < 400:
            arr = cv2.resize(arr, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        arr = cv2.copyMakeBorder(arr, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
        text, _, _ = detector.detectAndDecode(arr)
        if text:
            try:
                out.append({"raw": text, "json": json.loads(text)})
            except json.JSONDecodeError:
                out.append({"raw": text, "json": None})
    return out


# ---------------------------------------------------------------- classification
CLASS_PROFILES: dict[str, list[tuple[str, float]]] = {
    "APPLICATION_FORM": [("extension of approval", 3), ("application form", 3), ("intake", 1.5),
                         ("programme", 1), ("payment reference", 1.5), ("academic year", 1)],
    "FIRE_SAFETY_CERTIFICATE": [("fire", 2.5), ("no objection certificate", 1.5), ("fire safety", 3),
                                ("fire services", 2), ("extinguish", 1)],
    "STRUCTURAL_STABILITY_CERTIFICATE": [("structural stability", 4), ("structural", 1.5),
                                         ("load bearing", 1.5), ("stability", 1.5)],
    "OCCUPANCY_CERTIFICATE": [("occupancy certificate", 4), ("occupancy", 2), ("completion", 1)],
    "ACCREDITATION_CERTIFICATE": [("accreditation", 4), ("accredited", 2), ("programmes covered", 1.5)],
    "AFFIDAVIT": [("affidavit", 4), ("deponent", 2), ("solemnly", 2), ("notar", 1.5)],
    "LAND_DOCUMENT": [("land", 1.5), ("registered deed", 3), ("survey no", 2), ("lease deed", 3)],
    "BUILDING_PLAN": [("building plan", 4), ("floor plan", 3), ("sanctioned plan", 3)],
    "FACULTY_LIST": [("faculty", 3), ("designation", 1), ("qualification", 1.5)],
}
IMAGE_TYPES = {"INFRASTRUCTURE_PHOTO", "BUILDING_PLAN"}


def classify(text: str, declared: str | None, content_type: str) -> tuple[str, float, dict]:
    t = text.lower()
    scores = {k: sum(w for kw, w in kws if kw in t) for k, kws in CLASS_PROFILES.items()}
    if content_type.startswith("image/") and not t.strip():
        dt = declared if declared in IMAGE_TYPES else "INFRASTRUCTURE_PHOTO"
        return dt, 0.5 if declared else 0.3, scores
    best = max(scores, key=scores.get)
    total = sum(scores.values())
    if scores[best] == 0:
        return (declared or "UNKNOWN"), 0.2 if declared else 0.0, scores
    conf = round(min(0.99, scores[best] / total * min(1.0, scores[best] / 5)), 3)
    return best, conf, scores


# ---------------------------------------------------------------- field schemas
D = DATE_PATTERN
SEP = r"\s*[:\-–]\s*"
CERT_FIELDS = {
    "certificate_number": [rf"Certificate\s+(?:No\.?|Number){SEP}(?P<v>[A-Z0-9][A-Z0-9/\-]+)"],
    "institution_name": [rf"(?:Issued to|Name of (?:the )?(?:Institution|Occupier|Premises Holder|Owner)){SEP}(?P<v>[^\n]+)"],
    "issuer": [rf"Issuing Authority{SEP}(?P<v>[^\n]+)"],
    "issue_date": [rf"(?:Date of Issue|Issue Date|Issued on){SEP}(?P<v>{D})"],
    "valid_until": [rf"(?:Valid\s+(?:Up\s?to|Until|Till)|Expiry Date|Date of Expiry){SEP}(?P<v>{D})"],
    "premises_address": [rf"(?:Premises|Address of Premises|Address){SEP}(?P<v>[^\n]+)"],
}
SCHEMAS: dict[str, dict[str, dict]] = {
    "APPLICATION_FORM": {
        "institution_name": {"type": "text", "required": True,
                             "patterns": [rf"Name of (?:the )?Institution{SEP}(?P<v>[^\n]+)"]},
        "institution_code": {"type": "id", "required": True,
                             "patterns": [rf"Institution (?:ID|Code){SEP}(?P<v>[A-Z0-9\-]+)"]},
        "application_category": {"type": "text", "required": True,
                                 "patterns": [rf"Application (?:Category|Type){SEP}(?P<v>[^\n]+)"]},
        "academic_year": {"type": "text", "required": True,
                          "patterns": [rf"Academic Year{SEP}(?P<v>\d{{4}}-\d{{2,4}})"]},
        "payment_reference": {"type": "id", "required": True,
                              "patterns": [rf"(?:Payment|Fee)\s+(?:Reference|Transaction)\s*(?:No\.?|Number|ID)?{SEP}(?P<v>[A-Z0-9\-]+)"]},
        "payment_amount": {"type": "int", "required": True,
                           "patterns": [rf"Amount Paid{SEP}(?:INR|Rs\.?)?\s*(?P<v>[\d,]+)"]},
        "submission_date": {"type": "date", "required": False,
                            "patterns": [rf"Date of Submission{SEP}(?P<v>{D})"]},
        "programmes": {"type": "programmes", "required": True, "patterns": []},
    },
    "FIRE_SAFETY_CERTIFICATE": {k: {"type": "date" if "date" in k or k == "valid_until" else "text",
                                    "required": k != "premises_address", "patterns": v}
                                for k, v in CERT_FIELDS.items()},
    "STRUCTURAL_STABILITY_CERTIFICATE": {k: {"type": "date" if "date" in k or k == "valid_until" else "text",
                                             "required": k not in ("premises_address", "valid_until"), "patterns": v}
                                         for k, v in CERT_FIELDS.items()},
    "OCCUPANCY_CERTIFICATE": {k: {"type": "date" if "date" in k or k == "valid_until" else "text",
                                  "required": k in ("certificate_number", "institution_name", "issue_date", "issuer"),
                                  "patterns": v}
                              for k, v in CERT_FIELDS.items()},
    "ACCREDITATION_CERTIFICATE": {
        **{k: {"type": "date" if "date" in k or k == "valid_until" else "text",
               "required": k != "premises_address", "patterns": v} for k, v in CERT_FIELDS.items()},
        "validity_period_years": {"type": "int", "required": False,
                                  "patterns": [r"valid\s+for\s+(?:a\s+period\s+of\s+)?(?P<v>\d+)\s*(?:\(\w+\))?\s*years?"]},
        "programmes_covered": {"type": "text", "required": False,
                               "patterns": [rf"Programmes Covered{SEP}(?P<v>[^\n]+)"]},
    },
    "AFFIDAVIT": {
        "institution_name": {"type": "text", "required": True,
                             "patterns": [r"on behalf of (?:the )?(?P<v>[^,\n]+)"]},
        "deponent_designation": {"type": "text", "required": True,
                                 "patterns": [rf"Designation{SEP}(?P<v>[^\n]+)", r"working as (?:the )?(?P<v>[^,\n]+)"]},
        "notarised_date": {"type": "date", "required": True,
                           "patterns": [rf"(?:Notari[sz]ed on|Date of Notarisation|Sworn on)(?:{SEP}|\s+)(?P<v>{D})"]},
        "stamp_paper_number": {"type": "id", "required": False,
                               "patterns": [rf"Stamp Paper (?:No\.?|Number){SEP}(?P<v>[A-Z0-9\-]+)"]},
    },
}
PROGRAMME_RE = re.compile(
    r"(?P<prog>(?:B\.?\s?Tech|M\.?\s?Tech|MBA|MCA|B\.?\s?Arch|Diploma|B\.?\s?Pharm|PGDM)[^|\n]*?)\s*\|\s*"
    r"(?:Approved\s+|Proposed\s+)?Intake\s*[:\-]?\s*(?P<intake>\d{1,4})",
    re.IGNORECASE,
)


def _normalize(ftype: str, value: str) -> tuple[str | None, bool]:
    value = value.strip().strip(".")
    if ftype == "date":
        d = parse_date(value)
        return (d.isoformat() if d else None), d is not None
    if ftype == "int":
        digits = re.sub(r"[^\d]", "", value)
        return (str(int(digits)) if digits else None), bool(digits)
    if ftype == "id":
        return value.upper(), bool(value)
    return re.sub(r"\s+", " ", value), bool(value)


def _locate(pages: list[Page], snippet: str) -> tuple[int | None, list[float] | None]:
    for p in pages:
        for ln in p.lines:
            if snippet and snippet[:40] in ln.text:
                return p.page_no, ln.bbox
        if snippet and snippet in p.text:
            return p.page_no, None
    return None, None


def extract_fields(doc_type: str, pages: list[Page]) -> tuple[list[FieldValue], list[str]]:
    schema = SCHEMAS.get(doc_type)
    if not schema:
        return [], []
    full = "\n".join(p.text for p in pages)
    base_conf = {"pdf-text-layer": 0.95, "synthetic-fixture": 1.0}
    out: list[FieldValue] = []
    errors: list[str] = []
    for name, spec in schema.items():
        if spec["type"] == "programmes":
            progs = []
            for m in PROGRAMME_RE.finditer(full):
                progs.append({"programme": re.sub(r"\s+", " ", m.group("prog")).strip(),
                              "intake": int(m.group("intake"))})
            if progs:
                pg, bbox = _locate(pages, progs[0]["programme"])
                method = pages[(pg or 1) - 1].method if pages else "none"
                out.append(FieldValue(name, json.dumps(progs), json.dumps(progs),
                                      base_conf.get(method, (pages[0].confidence or 0.5) if pages else 0.0),
                                      pg, bbox, method))
            elif spec["required"]:
                errors.append(f"Required field '{name}' not found")
            continue
        found = False
        for pat in spec["patterns"]:
            m = re.search(pat, full, re.IGNORECASE)
            if not m:
                continue
            raw = m.group("v").strip()
            norm, ok = _normalize(spec["type"], raw)
            pg, bbox = _locate(pages, m.group(0).strip()[:60])
            method = pages[(pg or 1) - 1].method if pages else "none"
            conf = base_conf.get(method, (pages[(pg or 1) - 1].confidence or 0.5))
            if not ok:
                conf *= 0.3
                errors.append(f"Field '{name}' value '{raw[:40]}' could not be parsed as {spec['type']}")
            out.append(FieldValue(name, raw, norm, round(conf, 3), pg, bbox, method))
            found = True
            break
        if not found and spec["required"]:
            errors.append(f"Required field '{name}' not found")
    errors += _schema_check(doc_type, out)
    return out, errors


_PY_TYPES = {"text": str, "id": str, "date": date, "int": int, "programmes": list}


def _schema_check(doc_type: str, fields: list[FieldValue]) -> list[str]:
    """Validate the extracted record against a typed pydantic schema (schema-constrained output)."""
    schema = SCHEMAS[doc_type]
    model: type[BaseModel] = create_model(  # type: ignore[call-overload]
        f"{doc_type.title()}Record",
        **{n: ((_PY_TYPES[s["type"]] if s["required"] else _PY_TYPES[s["type"]] | None), ... if s["required"] else None)
           for n, s in schema.items()},
    )
    record: dict[str, Any] = {}
    for f in fields:
        if f.normalized is None:
            continue
        record[f.name] = json.loads(f.normalized) if schema[f.name]["type"] == "programmes" else f.normalized
    try:
        model.model_validate(record)
        return []
    except ValidationError as exc:
        return [f"Schema: {'.'.join(map(str, e['loc']))} — {e['msg']}" for e in exc.errors()
                if "Field required" not in e["msg"]]


# ---------------------------------------------------------------- entry point
def process(data: bytes, content_type: str, declared_type: str | None = None,
            fixture_pages: list[str] | None = None) -> ExtractionResult:
    warnings: list[str] = []
    if content_type == "application/pdf":
        pages, meta = _extract_pdf(data)
    else:
        pages, meta = _image_page(data)

    if fixture_pages is not None:
        # Labelled synthetic fixture text (demo speed). Never reported as OCR output.
        for i, txt in enumerate(fixture_pages):
            if i < len(pages) and len(pages[i].text.strip()) < 20:
                pages[i].text = txt
                pages[i].lines = [Line(t, [0.08, 0.1 + j * 0.03, 0.9, 0.12 + j * 0.03])
                                  for j, t in enumerate(txt.splitlines())]
                pages[i].method, pages[i].confidence = "synthetic-fixture", 1.0
        warnings.append("Synthetic fixture text used for pages without a text layer")
    if declared_type not in IMAGE_TYPES or content_type == "application/pdf":
        _apply_ocr(pages, warnings)  # photographs are analysed visually, not read

    all_images = [img for p in pages for img in p.images]
    qr = decode_qr_codes(all_images) if all_images else []
    full_text = "\n".join(p.text for p in pages)
    doc_type, cconf, scores = classify(full_text, declared_type, content_type)
    fields, schema_errors = extract_fields(doc_type, pages)

    methods = {p.method for p in pages}
    method = methods.pop() if len(methods) == 1 else "mixed"
    confs = [p.confidence for p in pages if p.confidence is not None]
    conf = round(sum(confs) / len(confs), 3) if confs else 0.0
    return ExtractionResult(pages, meta, doc_type, cconf, scores, fields, schema_errors, qr, method, conf, warnings)
