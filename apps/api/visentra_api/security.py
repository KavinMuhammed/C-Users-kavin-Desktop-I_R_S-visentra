"""Authentication, sessions, CSRF, rate limiting and project-level authorization."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import Project, ProjectMember, User, UserSession

SESSION_COOKIE = "visentra_session"
CSRF_COOKIE = "visentra_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# ---------------------------------------------------------------- passwords (scrypt, stdlib)
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


_DUMMY_HASH = hash_password(secrets.token_urlsafe(12))


def password_problems(pw: str) -> str | None:
    if len(pw) < 10:
        return "Use at least 10 characters."
    if len(pw) > 200:
        return "Password is too long."
    if not any(c.isdigit() for c in pw) or not any(c.isalpha() for c in pw):
        return "Include at least one letter and one number."
    return None


def token_hash(token: str) -> str:
    return hmac.new(settings.session_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- rate limiting (in-process sliding window)
class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_s: float) -> None:
        nowt = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= nowt - window_s:
                q.popleft()
            if len(q) >= limit:
                retry = int(window_s - (nowt - q[0])) + 1
                raise HTTPException(429, detail={"code": "RATE_LIMITED", "message": f"Too many requests. Try again in {retry} s."}, headers={"Retry-After": str(retry)})
            q.append(nowt)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------- sessions
def create_session(db: Session, user: User, request: Request) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(32)
    sess = UserSession(
        user_id=user.id,
        token_hash=token_hash(token),
        csrf_token=secrets.token_urlsafe(24),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.session_days),
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )
    db.add(sess)
    return token, sess


def set_session_cookies(response, token: str, csrf: str) -> None:
    max_age = settings.session_days * 86400
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=settings.cookie_secure, samesite="strict", path="/")


def clear_session_cookies(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_session(db: Session, request: Request) -> UserSession | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 200:
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash(token)))
    if not sess or sess.revoked_at or _aware(sess.expires_at) < datetime.now(timezone.utc):
        return None
    return sess


def current_session(request: Request, db: Session = Depends(get_db)) -> UserSession:
    sess = load_session(db, request)
    if not sess:
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "message": "Sign in to continue."})
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        # compare_digest raises TypeError on non-ASCII str (header values are latin-1): that was a 500, not a 403
        if not sent or not sent.isascii() or not hmac.compare_digest(sent, sess.csrf_token):
            raise HTTPException(403, detail={"code": "CSRF", "message": "Security token missing or invalid. Reload the page and try again."})
    request.state.user_id = sess.user_id
    return sess


def current_user(sess: UserSession = Depends(current_session), db: Session = Depends(get_db)) -> User:
    user = db.get(User, sess.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "message": "Sign in to continue."})
    return user


# ---------------------------------------------------------------- project authorization
from . import permissions as P  # noqa: E402
from .models import Workspace, WorkspaceMember  # noqa: E402

ROLE_RANK = P.PROJECT_ROLE_RANK


def effective_project_role(db: Session, project: Project, user: User) -> str | None:
    """Stronger of explicit project membership and the role implied by workspace membership."""
    explicit = db.scalar(select(ProjectMember.role).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id))
    ws_role = None
    if project.workspace_id:
        wr = db.scalar(select(WorkspaceMember.role).where(WorkspaceMember.workspace_id == project.workspace_id, WorkspaceMember.user_id == user.id))
        ws_role = P.WORKSPACE_TO_PROJECT_ROLE.get(wr) if wr else None
    return P.stronger(explicit, ws_role)


def project_for_user(db: Session, project_id: str, user: User, min_role: str = "viewer", allow_archived: bool = False) -> Project:
    """Return the project only if the user's effective role is at least `min_role`.

    Unknown projects and projects the user cannot see both return 404, so IDs can't be probed
    across workspaces.
    """
    if not project_id or len(project_id) > 64:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Project not found."})
    project = db.get(Project, project_id)
    role = effective_project_role(db, project, user) if project else None
    if not project or role is None or (project.archived_at and not allow_archived):
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Project not found."})
    if project.archived_at and min_role != "viewer":
        raise HTTPException(409, detail={"code": "PROJECT_ARCHIVED", "message": "This project is archived and read-only."})
    if not P.has_rank(role, min_role):
        raise HTTPException(403, detail={"code": "FORBIDDEN", "message": "Your role on this project doesn't allow this action."})
    return project


def accessible_projects(db: Session, user: User, include_archived: bool = False) -> list[tuple[Project, str]]:
    """Every project the user can see, with their effective role (one query per source, no N+1)."""
    roles: dict[str, str | None] = {}
    for pid, role in db.execute(select(ProjectMember.project_id, ProjectMember.role).where(ProjectMember.user_id == user.id)):
        roles[pid] = P.stronger(roles.get(pid), role)
    ws = dict(db.execute(select(WorkspaceMember.workspace_id, WorkspaceMember.role).where(WorkspaceMember.user_id == user.id)).all())
    if ws:
        for pid, wid in db.execute(select(Project.id, Project.workspace_id).where(Project.workspace_id.in_(list(ws)))):
            roles[pid] = P.stronger(roles.get(pid), P.WORKSPACE_TO_PROJECT_ROLE.get(ws[wid]))
    if not roles:
        return []
    q = select(Project).where(Project.id.in_(list(roles)))
    if not include_archived:
        q = q.where(Project.archived_at.is_(None))
    return [(p, roles[p.id]) for p in db.scalars(q.order_by(Project.updated_at.desc()))]


def user_project_ids(db: Session, user: User) -> list[str]:
    return [p.id for p, _ in accessible_projects(db, user)]


def workspace_role(db: Session, workspace_id: str, user: User) -> str | None:
    return db.scalar(select(WorkspaceMember.role).where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user.id))


def workspace_for_user(db: Session, workspace_id: str, user: User, manage: bool = False) -> Workspace:
    ws = db.get(Workspace, workspace_id) if workspace_id and len(workspace_id) <= 64 else None
    role = workspace_role(db, ws.id, user) if ws else None
    if not ws or not role or ws.archived_at:
        raise HTTPException(404, detail={"code": "WORKSPACE_NOT_FOUND", "message": "Workspace not found."})
    if manage and "workspace.manage" not in P.WORKSPACE_ROLE_PERMISSIONS[role]:
        raise HTTPException(403, detail={"code": "FORBIDDEN", "message": "Only workspace owners and admins can do this."})
    return ws


def personal_workspace(db: Session, user: User) -> Workspace:
    """The user's personal workspace, created on first use."""
    ws = db.scalar(select(Workspace).where(Workspace.owner_id == user.id, Workspace.personal.is_(True)))
    if ws:
        return ws
    ws = Workspace(name=f"{(user.name or 'My').split(' ')[0]}'s workspace", owner_id=user.id, personal=True)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="OWNER"))
    db.flush()
    return ws
