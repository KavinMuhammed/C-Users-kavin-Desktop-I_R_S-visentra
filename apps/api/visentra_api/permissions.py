"""Role-based access control.

Two layers, both enforced server-side:
  * Workspace roles  — OWNER, ADMIN, ML_ENGINEER, REVIEWER, OPERATOR, VIEWER
  * Project roles    — owner, editor, operator, reviewer, viewer (explicit project membership)

A user's effective project role is the stronger of their explicit project membership and the
project role implied by their workspace role. Permissions are explicit strings; route handlers
check a minimum project role (rank) or a named permission.
"""
from __future__ import annotations

WORKSPACE_ROLES = ("OWNER", "ADMIN", "ML_ENGINEER", "REVIEWER", "OPERATOR", "VIEWER")

# project-level rank: higher includes everything below it
PROJECT_ROLE_RANK = {"viewer": 0, "reviewer": 1, "operator": 2, "editor": 3, "owner": 4}
WORKSPACE_TO_PROJECT_ROLE = {"OWNER": "owner", "ADMIN": "owner", "ML_ENGINEER": "editor", "OPERATOR": "operator", "REVIEWER": "reviewer", "VIEWER": "viewer"}

# Phase 10 inspection permissions (PRODUCT_REQUIREMENTS §3): inspections are workspace-scoped, so they are checked
# against the workspace role's set. VIEWER reads inspections and display copies; REVIEWER and above may fetch the
# original evidence; OPERATOR, ML_ENGINEER and OWNER/ADMIN create; only OWNER/ADMIN delete.
_VIEW = {"workspace.read", "project.read", "dataset.read", "model.read", "review.read", "device.read", "audit.read", "analytics.read", "lineage.read",
         "inspection.read", "media.read"}
_REVIEW = _VIEW | {"review.write", "media.read_original"}
_OPERATE = _REVIEW | {"inference.run", "device.manage", "inspection.create"}
_ENGINEER = _OPERATE | {"project.write", "dataset.write", "dataset.delete", "source.manage", "training.create", "training.cancel", "model.evaluate"}
_OWNER = _ENGINEER | {"project.delete", "project.manage_members", "model.promote", "model.retire", "settings.manage", "inspection.delete"}

PROJECT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset(_VIEW), "reviewer": frozenset(_REVIEW), "operator": frozenset(_OPERATE),
    "editor": frozenset(_ENGINEER), "owner": frozenset(_OWNER),
}
WORKSPACE_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    r: PROJECT_ROLE_PERMISSIONS[WORKSPACE_TO_PROJECT_ROLE[r]] | (frozenset({"workspace.manage"}) if r in ("OWNER", "ADMIN") else frozenset())
    for r in WORKSPACE_ROLES
}

# Phase 11 (PRODUCT_REQUIREMENTS §3): review and report permissions. They do not follow the cumulative project ranks
# (ML_ENGINEER may create reports but not decide findings), so they are granted per workspace role, additively.
_PHASE11 = {
    "OWNER": {"inspection.review", "report.create", "report.read"},
    "ADMIN": {"inspection.review", "report.create", "report.read"},
    "ML_ENGINEER": {"report.create", "report.read"},
    "OPERATOR": {"inspection.review", "report.create", "report.read"},
    "REVIEWER": {"inspection.review", "report.create", "report.read"},
    "VIEWER": {"report.read"},
}
WORKSPACE_ROLE_PERMISSIONS = {r: perms | frozenset(_PHASE11[r]) for r, perms in WORKSPACE_ROLE_PERMISSIONS.items()}


def stronger(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if PROJECT_ROLE_RANK[a] >= PROJECT_ROLE_RANK[b] else b


def has_rank(role: str | None, min_role: str) -> bool:
    return role is not None and PROJECT_ROLE_RANK.get(role, -1) >= PROJECT_ROLE_RANK[min_role]


def can(role: str | None, permission: str) -> bool:
    return role is not None and permission in PROJECT_ROLE_PERMISSIONS.get(role, frozenset())


def workspace_can(role: str | None, permission: str) -> bool:
    return role is not None and permission in WORKSPACE_ROLE_PERMISSIONS.get(role, frozenset())
