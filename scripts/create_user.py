"""Operator account tool for servers with REGISTRATION_OPEN=false (staging, production).

    python scripts/create_user.py --email engineer@example.org --name "A. Engineer"            # create
    python scripts/create_user.py --email engineer@example.org --reset                          # set a new password

Run it on the server, from the install directory, with the server's .env (it uses the same database). The password is
read without echo, or from --password-file (deleted by you afterwards); it is never taken from the command line, so it
does not end up in shell history or the process list. A new account gets its own personal workspace, like sign-up.
A reset revokes every session of that account. Both actions are written to the audit log.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", help="display name (required when creating)")
    ap.add_argument("--reset", action="store_true", help="set a new password for an existing account")
    ap.add_argument("--password-file", help="read the password from this file instead of prompting")
    a = ap.parse_args()

    from sqlalchemy import select

    from visentra_api.audit import audit
    from visentra_api.config import settings
    from visentra_api.db import SessionLocal
    from visentra_api.main import migrate
    from visentra_api.models import User, UserSession
    from visentra_api.security import hash_password, password_problems, personal_workspace

    # validate exactly as the sign-in API does (pydantic EmailStr → email-validator, no DNS lookup): an address the API
    # rejects (e.g. a reserved domain such as .test) would create an account that can never sign in
    from email_validator import EmailNotValidError, validate_email
    try:
        email = validate_email(a.email.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as ex:
        print(f"invalid email: {ex}", file=sys.stderr); return 2
    if a.password_file:
        pw = Path(a.password_file).read_text(encoding="utf-8").strip()
    else:
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Repeat: "):
            print("passwords differ", file=sys.stderr); return 2
    if problem := password_problems(pw):
        print(problem, file=sys.stderr); return 2

    migrate()                                   # same schema the server uses (no-op when current)
    db = SessionLocal()
    try:
        u = db.scalar(select(User).where(User.email == email))
        now = datetime.now(timezone.utc)
        if a.reset:
            if u is None:
                print("no such account", file=sys.stderr); return 1
            u.password_hash = hash_password(pw)
            for s in db.scalars(select(UserSession).where(UserSession.user_id == u.id, UserSession.revoked_at.is_(None))):
                s.revoked_at = now
            audit(db, "auth.password_reset_by_operator", "user", u.id, user_id=None, meta={"tool": "scripts/create_user.py"})
            db.commit()
            print(f"password reset for {email}; all its sessions were revoked")
            return 0
        if u is not None:
            print("an account with this email already exists (use --reset)", file=sys.stderr); return 1
        if not a.name or not (2 <= len(a.name.strip()) <= 120):
            print("--name (2-120 characters) is required", file=sys.stderr); return 2
        u = User(email=email, name=a.name.strip(), password_hash=hash_password(pw))
        db.add(u)
        db.flush()
        ws = personal_workspace(db, u)
        audit(db, "auth.user_created_by_operator", "user", u.id, user_id=None, meta={"tool": "scripts/create_user.py", "workspace_id": ws.id})
        db.commit()
        print(f"created {email} (environment {settings.env}, workspace {ws.name!r})")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
