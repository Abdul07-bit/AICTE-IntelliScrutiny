"""Deterministic regulatory rules engine.

Rules are structured JSON conditions (no eval of arbitrary code). Every operator returns
PASS / FAIL / INDETERMINATE / NOT_APPLICABLE together with the observed and expected values
so findings can be traced to exact extracted fields."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from datetime import date
from typing import Any, Callable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Rule
from app.services.textutil import name_similarity, normalize_name, parse_date

PASS, FAIL, INDET, NA = "PASS", "FAIL", "INDETERMINATE", "NOT_APPLICABLE"


@dataclass
class Outcome:
    result: str
    op: str
    reason: str
    observed: Any = None
    expected: Any = None
    document_id: str | None = None
    doc_type: str | None = None
    field: str | None = None
    page: int | None = None
    bbox: list | None = None
    extra: dict = dc_field(default_factory=dict)


@dataclass
class RuleResult:
    rule_code: str
    version: int
    result: str
    outcomes: list[Outcome]

    def to_dict(self) -> dict:
        return {"rule_code": self.rule_code, "version": self.version, "result": self.result,
                "outcomes": [asdict(o) for o in self.outcomes]}


def _resolve(ctx: dict, path: str) -> Any:
    cur: Any = ctx
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        else:
            return None
    return cur


def _docs(ctx: dict, doc_type: str) -> list[dict]:
    if doc_type == "*":
        return [d for ds in ctx.get("documents", {}).values() for d in ds]
    return ctx.get("documents", {}).get(doc_type, [])


def _field(doc: dict, name: str) -> dict | None:
    return (doc.get("fields") or {}).get(name)


def _loc(doc: dict, f: dict | None) -> dict:
    return {"document_id": doc.get("id"), "doc_type": doc.get("doc_type"),
            "page": (f or {}).get("page"), "bbox": (f or {}).get("bbox")}


# ---------------------------------------------------------------- operators
def op_documents_present(node, ctx) -> list[Outcome]:
    missing = [t for t in node["doc_types"] if not _docs(ctx, t)]
    present = [t for t in node["doc_types"] if _docs(ctx, t)]
    if missing:
        return [Outcome(FAIL, "documents_present", f"Required document(s) not found: {', '.join(missing)}",
                        observed=present, expected=node["doc_types"])]
    return [Outcome(PASS, "documents_present", "All required documents present", observed=present,
                    expected=node["doc_types"])]


def op_field_present(node, ctx) -> list[Outcome]:
    docs = _docs(ctx, node["doc_type"])
    if not docs:
        return [Outcome(NA, "field_present", f"No {node['doc_type']} submitted")]
    out = []
    for d in docs:
        f = _field(d, node["field"])
        ok = bool(f and f.get("value"))
        out.append(Outcome(PASS if ok else FAIL, "field_present",
                           f"Field '{node['field']}' {'present' if ok else 'missing or unreadable'}",
                           observed=(f or {}).get("value"), expected="non-empty", field=node["field"], **_loc(d, f)))
    return out


def op_date_on_or_after(node, ctx) -> list[Outcome]:
    ref = parse_date(str(_resolve(ctx, node["ref"]) or ""))
    docs = _docs(ctx, node["doc_type"])
    if not docs:
        return [Outcome(NA, "date_on_or_after", f"No {node['doc_type']} submitted")]
    if ref is None:
        return [Outcome(INDET, "date_on_or_after", f"Reference date '{node['ref']}' unavailable")]
    out = []
    for d in docs:
        f = _field(d, node["field"])
        val = parse_date((f or {}).get("normalized"))
        if val is None:
            out.append(Outcome(INDET, "date_on_or_after", f"'{node['field']}' could not be read",
                               observed=(f or {}).get("value"), expected=f">= {ref.isoformat()}",
                               field=node["field"], **_loc(d, f)))
        else:
            ok = val >= ref
            out.append(Outcome(PASS if ok else FAIL, "date_on_or_after",
                               f"{node['field']} {val.isoformat()} is {'on/after' if ok else 'before'} "
                               f"reference date {ref.isoformat()} ({node['ref']})",
                               observed=val.isoformat(), expected=f">= {ref.isoformat()}",
                               field=node["field"], **_loc(d, f), extra={"days_short": (ref - val).days if not ok else 0}))
    return out


def op_date_within_days(node, ctx) -> list[Outcome]:
    ref = parse_date(str(_resolve(ctx, node["ref"]) or ""))
    docs = _docs(ctx, node["doc_type"])
    if not docs:
        return [Outcome(NA, "date_within_days", f"No {node['doc_type']} submitted")]
    out = []
    for d in docs:
        f = _field(d, node["field"])
        val = parse_date((f or {}).get("normalized"))
        if val is None or ref is None:
            out.append(Outcome(INDET, "date_within_days", f"'{node['field']}' or reference date unavailable",
                               observed=(f or {}).get("value"), field=node["field"], **_loc(d, f)))
            continue
        age = (ref - val).days
        ok = 0 <= age <= node["max_days"]
        out.append(Outcome(PASS if ok else FAIL, "date_within_days",
                           f"{node['field']} is {age} days before {node['ref']} (allowed 0–{node['max_days']})",
                           observed=val.isoformat(), expected=f"within {node['max_days']} days of {ref.isoformat()}",
                           field=node["field"], **_loc(d, f)))
    return out


def op_name_matches(node, ctx) -> list[Outcome]:
    expected = _resolve(ctx, node["ref"])
    aliases = _resolve(ctx, node.get("aliases_ref", "application.institution_aliases")) or []
    threshold = node.get("min_similarity", 0.93)
    out = []
    for dt in node.get("doc_types", [node.get("doc_type", "*")]):
        for d in _docs(ctx, dt):
            f = _field(d, node["field"])
            if not f or not f.get("value"):
                continue
            candidates = [expected, *aliases]
            exact = any(normalize_name(f["value"]) == normalize_name(c) for c in candidates if c)
            sim = max((name_similarity(f["value"], c) for c in candidates if c), default=0.0)
            res = PASS if exact else (INDET if sim >= threshold else FAIL)
            out.append(Outcome(res, "name_matches",
                               "Exact match with registered name" if exact else
                               f"Name differs from registered institution name (similarity {sim:.2f}, threshold {threshold})",
                               observed=f["value"], expected=expected, field=node["field"], **_loc(d, f),
                               extra={"similarity": sim}))
    return out or [Outcome(NA, "name_matches", "No documents carry the field")]


def _norm_prog(p: str) -> str:
    return normalize_name(p).replace("b tech", "btech").replace("m tech", "mtech")


def op_intake_within_approved(node, ctx) -> list[Outcome]:
    declared = _resolve(ctx, node.get("declared_ref", "documents.APPLICATION_FORM.0.fields.programmes.normalized"))
    form = (_docs(ctx, "APPLICATION_FORM") or [{}])[0]
    pf = _field(form, "programmes") if form else None
    if isinstance(declared, str):
        declared = json.loads(declared)
    records = ctx.get("records", {}).get(node.get("record_type", "PRIOR_APPROVAL"), [])
    if not declared:
        return [Outcome(INDET, "intake_within_approved", "Declared programmes could not be extracted")]
    if not records:
        return [Outcome(INDET, "intake_within_approved", "No prior authorized approval record available")]
    latest = sorted(records, key=lambda r: r.get("academic_year") or "")[-1]
    approved = {_norm_prog(p["programme"]): p for p in latest.get("programmes", [])}
    out = []
    for p in declared:
        key = _norm_prog(p["programme"])
        prior = approved.get(key)
        loc = _loc(form, pf)
        meta = {"record_reference": latest.get("reference"), "record_year": latest.get("academic_year"),
                "record_id": latest.get("id")}
        if prior is None:
            out.append(Outcome(FAIL, "intake_within_approved",
                               f"Programme '{p['programme']}' not found in prior approval {latest.get('reference')}",
                               observed=p, expected="programme present in prior approval", field="programmes", **loc, extra=meta))
        elif p["intake"] > prior["intake"]:
            out.append(Outcome(FAIL, "intake_within_approved",
                               f"Declared intake {p['intake']} for '{p['programme']}' exceeds approved intake {prior['intake']} "
                               f"({latest.get('reference')}, {latest.get('academic_year')})",
                               observed=p["intake"], expected=f"<= {prior['intake']}", field="programmes", **loc, extra=meta))
        else:
            out.append(Outcome(PASS, "intake_within_approved",
                               f"'{p['programme']}' intake {p['intake']} within approved {prior['intake']}",
                               observed=p["intake"], expected=f"<= {prior['intake']}", field="programmes", **loc, extra=meta))
    return out


def op_equals(node, ctx) -> list[Outcome]:
    left = _resolve(ctx, node["left"])
    right = _resolve(ctx, node["right"])
    if left is None or right is None:
        return [Outcome(INDET, "equals", f"Value unavailable for comparison ({node['left']} vs {node['right']})",
                        observed=left, expected=right)]
    ok = str(left).strip().upper() == str(right).strip().upper()
    return [Outcome(PASS if ok else FAIL, "equals",
                    node.get("label", "Exact comparison") + (" matches" if ok else " does not match"),
                    observed=left, expected=right)]


def op_min_count(node, ctx) -> list[Outcome]:
    n = len(_docs(ctx, node["doc_type"]))
    ok = n >= node["min"]
    return [Outcome(PASS if ok else FAIL, "min_count",
                    f"{n} {node['doc_type']} item(s) submitted; at least {node['min']} expected",
                    observed=n, expected=f">= {node['min']}")]


OPS: dict[str, Callable[[dict, dict], list[Outcome]]] = {
    "documents_present": op_documents_present,
    "field_present": op_field_present,
    "date_on_or_after": op_date_on_or_after,
    "date_within_days": op_date_within_days,
    "name_matches": op_name_matches,
    "intake_within_approved": op_intake_within_approved,
    "equals": op_equals,
    "min_count": op_min_count,
}


def _eval(node: dict, ctx: dict) -> list[Outcome]:
    if "all" in node or "any" in node:
        children = [o for child in node.get("all", node.get("any", [])) for o in _eval(child, ctx)]
        return children
    op = node.get("op")
    if op not in OPS:
        return [Outcome(INDET, str(op), f"Unknown operator '{op}' — rule needs administrator review")]
    return OPS[op](node, ctx)


def aggregate(node: dict, outcomes: list[Outcome]) -> str:
    results = [o.result for o in outcomes if o.result != NA]
    if not results:
        return NA
    if "any" in node:
        return PASS if PASS in results else (INDET if INDET in results else FAIL)
    if FAIL in results:
        return FAIL
    if INDET in results:
        return INDET
    return PASS


def evaluate(rule: Rule, ctx: dict) -> RuleResult:
    outcomes = _eval(rule.condition or {}, ctx)
    return RuleResult(rule.rule_code, rule.version, aggregate(rule.condition or {}, outcomes), outcomes)


def validate_condition(node: dict) -> list[str]:
    errors = []
    if "all" in node or "any" in node:
        children = node.get("all", node.get("any"))
        if not isinstance(children, list) or not children:
            errors.append("'all'/'any' must contain a non-empty list")
        else:
            for c in children:
                errors += validate_condition(c)
    elif node.get("op") not in OPS:
        errors.append(f"Unknown operator: {node.get('op')!r}. Allowed: {sorted(OPS)}")
    return errors


def run_tests(rule: Rule) -> list[dict]:
    out = []
    for tc in rule.test_cases or []:
        got = evaluate(rule, tc.get("context", {}))
        out.append({"name": tc.get("name"), "expected": tc.get("expected"), "actual": got.result,
                    "passed": got.result == tc.get("expected"), "outcomes": [asdict(o) for o in got.outcomes]})
    return out


def applicable_rules(db: Session, category: str, as_of: date) -> list[Rule]:
    """Latest APPROVED version of each rule code whose effective window covers `as_of`."""
    rows = db.scalars(
        select(Rule).where(
            Rule.status == "APPROVED",
            Rule.category.in_([category, "ALL"]),
            or_(Rule.effective_from.is_(None), Rule.effective_from <= as_of),
            or_(Rule.effective_to.is_(None), Rule.effective_to >= as_of),
        ).order_by(Rule.rule_code, Rule.version.desc())
    ).all()
    latest: dict[str, Rule] = {}
    for r in rows:
        latest.setdefault(r.rule_code, r)
    return list(latest.values())
