"""Prototype evaluation against freshly generated, labelled synthetic fixtures.

IMPORTANT: fixtures come from the same template family the extractor was written against, so
these numbers measure pipeline correctness on synthetic data. They are NOT evidence of accuracy
on real institutional documents or of improvements to real AICTE processing."""
from __future__ import annotations

import random
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.demo import fixtures as fx
from app.models import EvaluationRun
from app.rules.engine import op_name_matches
from app.services import extraction, storage
from app.services.textutil import text_similarity

HOLDERS = ["Northfield Institute of Engineering", "Riverbend College of Technology", "Lakeside School of Management",
           "Hillcrest Polytechnic", "Meadowvale Institute of Pharmacy", "Eastgate College of Architecture"]
VARIANTS = {"Northfield Institute of Engineering": "Northfield Institute of Engineering and Research",
            "Riverbend College of Technology": "Riverbend Technical College",
            "Lakeside School of Management": "Lakeside School of Management Studies",
            "Hillcrest Polytechnic": "Hillcrest Polytechnic College",
            "Meadowvale Institute of Pharmacy": "Meadowvale Pharmacy Institute",
            "Eastgate College of Architecture": "Eastgate College of Planning"}
CERT_TYPES = {
    "FIRE_SAFETY_CERTIFICATE": ("Fire Safety Certificate", "Synthetic Fire Services (Mock)",
                                ["Fire safety inspection found extinguishers and exits in order."]),
    "STRUCTURAL_STABILITY_CERTIFICATE": ("Structural Stability Certificate", "Synthetic Engineering Dept (Mock)",
                                         ["Structural stability of load bearing elements assessed."]),
    "ACCREDITATION_CERTIFICATE": ("Certificate of Accreditation", "Synthetic Accreditation Council (Mock)",
                                  ["Programmes Covered: B.Tech CSE", "The programmes are accredited."]),
    "OCCUPANCY_CERTIFICATE": ("Occupancy Certificate", "Synthetic Urban Local Body (Mock)",
                              ["Building completed and fit for occupancy."]),
}
FIELDS = ["certificate_number", "institution_name", "issue_date", "valid_until"]


def _pr(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    return {"precision": round(p, 4) if p is not None else None, "recall": round(r, 4) if r is not None else None,
            "tp": tp, "fp": fp, "fn": fn}


def _gen_cert(rng: random.Random, i: int, holder: str) -> tuple[bytes, str, dict]:
    dtype = rng.choice(list(CERT_TYPES))
    heading, issuer, body = CERT_TYPES[dtype]
    issued = date(2024, 1, 1) + timedelta(days=rng.randint(0, 900))
    valid = issued + timedelta(days=365 * rng.randint(1, 5))
    cert_no = f"EV-{dtype[:3]}-{rng.randint(1000, 9999)}-{i}"
    fmt = rng.choice(["%d-%m-%Y", "%d/%m/%Y", "%d %B %Y", "%Y-%m-%d"])  # vary printed date formats
    data = fx.certificate(heading, issuer, cert_no, holder, "Fictional address", issued.strftime("%d-%m-%Y"),
                          valid.strftime(fmt), body)
    truth = {"doc_type": dtype, "certificate_number": cert_no, "institution_name": holder,
             "issue_date": issued.isoformat(), "valid_until": valid.isoformat()}
    return data, dtype, truth


def run_evaluation(db: Session, user_id: str | None, samples: int = 24) -> EvaluationRun:
    rng = random.Random(samples)
    field_total = field_correct = cls_correct = 0
    per_field = {f: {"correct": 0, "total": 0} for f in FIELDS}
    mm = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    docs: list[tuple[bytes, dict, list[str]]] = []

    for i in range(samples):
        registered = rng.choice(HOLDERS)
        mismatch = rng.random() < 0.4
        holder = VARIANTS[registered] if mismatch else registered
        data, dtype, truth = _gen_cert(rng, i, holder)
        res = extraction.process(data, "application/pdf", None)
        cls_correct += res.doc_type == dtype
        got = {f.name: f.normalized for f in res.fields}
        for f in FIELDS:
            per_field[f]["total"] += 1
            field_total += 1
            ok = (got.get(f) or "").upper() == truth[f].upper() if f != "institution_name" else got.get(f) == truth[f]
            per_field[f]["correct"] += ok
            field_correct += ok
        ctx = {"application": {"institution_name": registered, "institution_aliases": []},
               "documents": {res.doc_type: [{"id": str(i), "doc_type": res.doc_type,
                                             "fields": {"institution_name": {"value": got.get("institution_name")}}}]}}
        flagged = any(o.result in ("FAIL", "INDETERMINATE")
                      for o in op_name_matches({"doc_types": [res.doc_type], "field": "institution_name",
                                                "ref": "application.institution_name"}, ctx))
        key = ("tp" if flagged else "fn") if mismatch else ("fp" if flagged else "tn")
        mm[key] += 1
        docs.append((data, truth, [p.text for p in res.pages]))

    # Required-document detection: random omission of required items per synthetic application.
    req = list(CERT_TYPES)[:2] + ["AFFIDAVIT", "APPLICATION_FORM"]
    rd = {"tp": 0, "fp": 0, "fn": 0}
    n_apps = max(4, samples // 3)
    for a in range(n_apps):
        present_truth = [t for t in req if rng.random() > 0.25]
        predicted_types = set()
        for t in present_truth:
            if t in CERT_TYPES:
                heading, issuer, body = CERT_TYPES[t]
                data = fx.certificate(heading, issuer, f"RD-{a}-{t[:3]}", "Fictional Institute", "Addr", "01-01-2026",
                                      "01-01-2029", body)
            elif t == "AFFIDAVIT":
                data = fx.make_pdf([["# Affidavit", "I, the deponent, on behalf of Fictional Institute, do solemnly affirm.",
                                     "Notarised on: 01-10-2026"]], title="affidavit")
            else:
                data = fx.application_form(fx.INST2, "2027-28", [("MBA", 60)], f"RD-PAY-{a}", "1,000", "01-10-2026")
            predicted_types.add(extraction.process(data, "application/pdf", None).doc_type)
        missing_truth = set(req) - set(present_truth)
        missing_pred = set(req) - predicted_types
        rd["tp"] += len(missing_truth & missing_pred)
        rd["fp"] += len(missing_pred - missing_truth)
        rd["fn"] += len(missing_truth - missing_pred)

    # Duplicate detection over labelled pairs: exact copy, re-rendered copy (new hash), distinct.
    dd = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for i in range(min(len(docs), samples)):
        data, truth, pages = docs[i]
        kind = ("exact", "near", "distinct")[i % 3]
        if kind == "exact":
            other, other_pages = data, pages
        elif kind == "near":
            heading, issuer, body = CERT_TYPES[truth["doc_type"]]
            other = fx.certificate(heading, issuer, truth["certificate_number"], truth["institution_name"],
                                   "Fictional address", date.fromisoformat(truth["issue_date"]).strftime("%d-%m-%Y"),
                                   date.fromisoformat(truth["valid_until"]).strftime("%d-%m-%Y"), body,
                                   creation="D:20261001120000")
            other_pages = [p.text for p in extraction.process(other, "application/pdf", None).pages]
        else:
            other, other_pages = docs[(i + 1) % len(docs)][0], docs[(i + 1) % len(docs)][2]
        predicted = storage.sha256(data) == storage.sha256(other) or \
            text_similarity("\n".join(pages), "\n".join(other_pages)) >= 0.9
        positive = kind != "distinct"
        dd[("tp" if predicted else "fn") if positive else ("fp" if predicted else "tn")] += 1

    metrics = {
        "ocr_field_extraction_accuracy": round(field_correct / field_total, 4) if field_total else None,
        "per_field_accuracy": {k: round(v["correct"] / v["total"], 4) for k, v in per_field.items() if v["total"]},
        "classification_accuracy": round(cls_correct / samples, 4) if samples else None,
        "required_document_detection": _pr(rd["tp"], rd["fp"], rd["fn"]),
        "cross_record_mismatch_detection": {**_pr(mm["tp"], mm["fp"], mm["fn"]), "tn": mm["tn"],
                                            "accuracy": round((mm["tp"] + mm["tn"]) / samples, 4) if samples else None},
        "duplicate_detection": {**_pr(dd["tp"], dd["fp"], dd["fn"]), "tn": dd["tn"]},
        "extraction_method": "pdf-text-layer (OCR path not exercised by these fixtures)"
                             + ("; Tesseract available" if extraction.tesseract_available() else "; Tesseract not installed"),
        "caveat": "Synthetic fixtures share templates with the extractor. Prototype evaluation metrics only — not "
                  "evidence of real-world accuracy or of improvements in real AICTE processing.",
    }
    counts = {"documents": samples, "fields": field_total, "applications_for_required_docs": n_apps,
              "required_document_slots": n_apps * len(req), "mismatch_cases": samples,
              "duplicate_pairs": min(len(docs), samples)}
    run = EvaluationRun(label=f"Synthetic fixture evaluation (n={samples})", metrics=metrics, sample_counts=counts,
                        pipeline_version=extraction.PIPELINE_VERSION, created_by_id=user_id, is_synthetic=True)
    db.add(run)
    db.flush()
    return run
