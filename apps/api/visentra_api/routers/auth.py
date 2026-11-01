from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import serializers as S
from ..audit import audit
from ..config import settings
from ..db import get_db
from ..models import PasswordReset, SystemSetting, User, UserSession
from ..models import Workspace, WorkspaceMember
from ..permissions import WORKSPACE_ROLE_PERMISSIONS
from ..security import (_DUMMY_HASH, personal_workspace, client_ip, clear_session_cookies, create_session, current_session, current_user, hash_password, limiter,
                        load_session, password_problems, set_session_cookies, token_hash, verify_password)

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger("visentra.auth")


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class PasswordIn(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=1, max_length=200)


class ProfileIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class PrefsIn(BaseModel):
    sound: bool | None = None
    theme: str | None = Field(default=None, pattern="^(dark|light|system)$")
    reduced_motion: bool | None = None
    # appearance / accessibility / sound preferences used by the web client
    density: str | None = Field(default=None, pattern="^(comfortable|compact)$")
    perf: str | None = Field(default=None, pattern="^(auto|high|balanced|low)$")
    bgfx: str | None = Field(default=None, pattern="^(on|subtle|off)$")
    accent: str | None = Field(default=None, pattern="^(full|soft)$")
    motion: str | None = Field(default=None, pattern="^(on|off)$")
    intro: bool | None = None
    ui_sounds: bool | None = None
    contrast: bool | None = None
    text_scale: int | None = Field(default=None, ge=100, le=125)
    volume: dict[str, float] | None = None
    workspace_id: str | None = Field(default=None, max_length=64)

    @field_validator("volume")
    @classmethod
    def _vol(cls, v):
        if v is None:
            return v
        allowed = {"master", "ui", "notifications", "training"}
        if set(v) - allowed or any(not (0 <= x <= 1) for x in v.values()):
            raise ValueError("Volume channels are master/ui/notifications/training with values between 0 and 1.")
        return v


def _err(status: int, code: str, message: str, field: str | None = None):
    raise HTTPException(status, detail={"code": code, "message": message, **({"field": field} if field else {})})


@router.post("/register")
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    if not settings.registration_open:
        _err(403, "REGISTRATION_CLOSED", "Registration is closed on this server.")
    limiter.check(f"register:{client_ip(request)}", 10, 3600)
    if problem := password_problems(body.password):
        _err(422, "VALIDATION", problem, "password")
    email = body.email.lower().strip()
    if db.scalar(select(User).where(User.email == email)):
        _err(409, "CONFLICT", "An account with this email already exists.", "email")
    u = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    db.add(u)
    db.flush()
    personal_workspace(db, u)
    token, sess = create_session(db, u, request)
    u.last_login_at = datetime.now(timezone.utc)
    audit(db, "auth.register", "user", u.id, user_id=u.id, ip=client_ip(request))
    db.commit()
    set_session_cookies(response, token, sess.csrf_token)
    return {"user": S.user(u), "csrf_token": sess.csrf_token}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    limiter.check(f"login-ip:{client_ip(request)}", 20, 300)
    limiter.check(f"login-email:{email}", 8, 300)
    u = db.scalar(select(User).where(User.email == email))
    ok = verify_password(body.password, u.password_hash if u else _DUMMY_HASH)
    if not u or not ok or not u.is_active:
        audit(db, "auth.login", "user", u.id if u else None, user_id=u.id if u else None, result="failure", ip=client_ip(request), meta={"reason": "invalid_credentials"})
        db.commit()
        _err(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
    token, sess = create_session(db, u, request)
    u.last_login_at = datetime.now(timezone.utc)
    audit(db, "auth.login", "user", u.id, user_id=u.id, ip=client_ip(request))
    db.commit()
    set_session_cookies(response, token, sess.csrf_token)
    return {"user": S.user(u), "csrf_token": sess.csrf_token}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    sess = load_session(db, request)
    if sess:
        sess.revoked_at = datetime.now(timezone.utc)
        audit(db, "auth.logout", "user", sess.user_id, user_id=sess.user_id, ip=client_ip(request))
        db.commit()
    clear_session_cookies(response)
    return {"ok": True}


@router.get("/me")
def me(sess: UserSession = Depends(current_session), db: Session = Depends(get_db)):
    u = db.get(User, sess.user_id)
    prefs = db.get(SystemSetting, f"prefs:{u.id}")
    ws = [{"id": w.id, "name": w.name, "personal": w.personal, "role": role, "permissions": sorted(WORKSPACE_ROLE_PERMISSIONS[role])}
          for w, role in db.execute(select(Workspace, WorkspaceMember.role).join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                                    .where(WorkspaceMember.user_id == u.id, Workspace.archived_at.is_(None)).order_by(Workspace.created_at))]
    return {"user": S.user(u), "csrf_token": sess.csrf_token, "workspaces": ws,
            "preferences": (prefs.value if prefs else {}) or {"sound": False, "theme": "dark"}}


@router.post("/refresh")
def refresh(request: Request, response: Response, sess: UserSession = Depends(current_session), db: Session = Depends(get_db)):
    """Rotate the session: a new token and CSRF secret replace the current ones (the old token stops working)."""
    u = db.get(User, sess.user_id)
    sess.revoked_at = datetime.now(timezone.utc)
    token, new = create_session(db, u, request)
    audit(db, "auth.refreshed", "user", u.id, user_id=u.id, ip=client_ip(request))
    db.commit()
    set_session_cookies(response, token, new.csrf_token)
    return {"user": S.user(u), "csrf_token": new.csrf_token, "expires_at": S.iso(new.expires_at)}


@router.patch("/me")
def update_me(body: ProfileIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    u.name = body.name.strip()
    audit(db, "user.updated", "user", u.id, user_id=u.id)
    db.commit()
    return {"user": S.user(u)}


@router.put("/me/preferences")
def prefs(body: PrefsIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(SystemSetting, f"prefs:{u.id}") or SystemSetting(key=f"prefs:{u.id}", value={})
    row.value = {**(row.value or {}), **body.model_dump(exclude_none=True)}
    db.merge(row)
    db.commit()
    return {"preferences": row.value}


@router.post("/me/password")
def change_password(body: PasswordIn, request: Request, sess: UserSession = Depends(current_session), db: Session = Depends(get_db)):
    u = db.get(User, sess.user_id)
    limiter.check(f"pw:{u.id}", 10, 900)
    if not verify_password(body.current, u.password_hash):
        _err(422, "VALIDATION", "Current password is incorrect.", "current")
    if problem := password_problems(body.new):
        _err(422, "VALIDATION", problem, "new")
    u.password_hash = hash_password(body.new)
    for s in db.scalars(select(UserSession).where(UserSession.user_id == u.id, UserSession.id != sess.id, UserSession.revoked_at.is_(None))):
        s.revoked_at = datetime.now(timezone.utc)
    audit(db, "auth.password_changed", "user", u.id, user_id=u.id, ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/forgot")
def forgot(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    limiter.check(f"forgot:{client_ip(request)}", 5, 900)
    u = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if u:
        token = secrets.token_urlsafe(32)
        db.add(PasswordReset(user_id=u.id, token_hash=token_hash(token), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        audit(db, "auth.password_reset_requested", "user", u.id, user_id=u.id, ip=client_ip(request))
        db.commit()
        # No mail transport is configured in this build: outside production the link is written to the server log for the
        # operator. In production it is not (PASSWORD_RESET_LINK_IN_LOG=1 opts in): log access must not grant account access.
        if settings.reset_link_in_log:
            log.warning("password reset link (deliver to user out-of-band): /#/reset/%s", token)
        else:
            log.warning("password reset requested for user %s; the link is not logged (PASSWORD_RESET_LINK_IN_LOG=0)", u.id)
    return {"ok": True, "message": "If an account exists for that email, a reset link has been issued."}


@router.post("/reset")
def reset(body: ResetIn, request: Request, db: Session = Depends(get_db)):
    limiter.check(f"reset:{client_ip(request)}", 10, 900)
    pr = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == token_hash(body.token)))
    exp = pr.expires_at if pr and pr.expires_at.tzinfo else (pr.expires_at.replace(tzinfo=timezone.utc) if pr else None)
    if not pr or pr.used_at or exp < datetime.now(timezone.utc):
        _err(400, "INVALID_TOKEN", "This reset link is invalid or has expired.")
    if problem := password_problems(body.password):
        _err(422, "VALIDATION", problem, "password")
    u = db.get(User, pr.user_id)
    u.password_hash = hash_password(body.password)
    pr.used_at = datetime.now(timezone.utc)
    for s in db.scalars(select(UserSession).where(UserSession.user_id == u.id, UserSession.revoked_at.is_(None))):
        s.revoked_at = datetime.now(timezone.utc)
    audit(db, "auth.password_reset", "user", u.id, user_id=u.id, ip=client_ip(request))
    db.commit()
    return {"ok": True}
