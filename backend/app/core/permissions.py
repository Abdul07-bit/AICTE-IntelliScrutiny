"""Role-based access control. Enforced in the backend for every route; the frontend only
uses the permission list to hide controls."""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    INSTITUTION_UPLOADER = "INSTITUTION_UPLOADER"
    SCRUTINY_OFFICER = "SCRUTINY_OFFICER"
    VERIFICATION_OFFICER = "VERIFICATION_OFFICER"
    REGULATORY_ADMIN = "REGULATORY_ADMIN"
    SYSTEM_ADMIN = "SYSTEM_ADMIN"
    AUDITOR = "AUDITOR"


class P(StrEnum):
    APP_READ = "application:read"
    APP_CREATE = "application:create"
    APP_SUBMIT = "application:submit"
    APP_PROCESS = "application:process"
    DOC_UPLOAD = "document:upload"
    DOC_READ = "document:read"
    FINDING_READ = "finding:read"
    FINDING_REVIEW = "finding:review"
    REVIEW_OUTCOME = "review:outcome"
    VERIFY_REQUEST = "verification:request"
    VERIFY_CONFIRM = "verification:confirm"
    SEARCH_REGULATORY = "search:regulatory"
    SEARCH_EVIDENCE = "search:evidence"
    RULE_READ = "rule:read"
    RULE_EDIT = "rule:edit"
    RULE_APPROVE = "rule:approve"
    SOURCE_MANAGE = "source:manage"
    CONNECTOR_READ = "connector:read"
    CONNECTOR_MANAGE = "connector:manage"
    AUDIT_READ = "audit:read"
    REPORT_READ = "report:read"
    EVAL_RUN = "evaluation:run"
    USER_MANAGE = "user:manage"
    SETTINGS_MANAGE = "settings:manage"
    DEMO_RUN = "demo:run"


_READ_ALL = {
    P.APP_READ, P.DOC_READ, P.FINDING_READ, P.RULE_READ, P.CONNECTOR_READ, P.REPORT_READ,
    P.SEARCH_REGULATORY, P.SEARCH_EVIDENCE,
}

ROLE_PERMISSIONS: dict[Role, set[P]] = {
    Role.INSTITUTION_UPLOADER: {
        P.APP_READ, P.APP_CREATE, P.APP_SUBMIT, P.DOC_UPLOAD, P.DOC_READ, P.FINDING_READ,
        P.SEARCH_EVIDENCE,
    },
    Role.SCRUTINY_OFFICER: _READ_ALL | {
        P.APP_PROCESS, P.FINDING_REVIEW, P.REVIEW_OUTCOME, P.VERIFY_REQUEST, P.DEMO_RUN,
        P.APP_CREATE, P.APP_SUBMIT, P.DOC_UPLOAD,
    },
    Role.VERIFICATION_OFFICER: _READ_ALL | {P.VERIFY_REQUEST, P.VERIFY_CONFIRM, P.FINDING_REVIEW},
    Role.REGULATORY_ADMIN: _READ_ALL | {P.RULE_EDIT, P.RULE_APPROVE, P.SOURCE_MANAGE, P.EVAL_RUN},
    Role.SYSTEM_ADMIN: _READ_ALL | {
        P.CONNECTOR_MANAGE, P.AUDIT_READ, P.USER_MANAGE, P.SETTINGS_MANAGE, P.EVAL_RUN,
        P.DEMO_RUN, P.APP_PROCESS,
    },
    Role.AUDITOR: _READ_ALL | {P.AUDIT_READ},
}

# Roles whose access is limited to their own institution's data.
TENANT_SCOPED_ROLES = {Role.INSTITUTION_UPLOADER}


def permissions_for(role: str) -> set[P]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return set()
