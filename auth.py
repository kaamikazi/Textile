from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from pathlib import Path

from database import Actor, ConflictError, ValidationError, fetch_one, transaction, utc_now, write_audit_log

PBKDF2_ITERATIONS = 600_000
ROLE_PERMISSIONS = {
    "Admin": {
        "view", "add_production", "add_expense", "add_attendance", "manage_records",
        "manage_employees", "manage_machines", "manage_backups", "restore_backups",
        "view_audit", "manage_users", "export",
    },
    "Staff": {"view", "add_production", "add_expense", "add_attendance", "export"},
}


@dataclass(frozen=True)
class AuthenticatedUser:
    id: int
    username: str
    role: str

    @property
    def actor(self) -> Actor:
        return Actor(self.username, self.role)


def _validate_username(username: str) -> str:
    cleaned = (username or "").strip()
    if len(cleaned) < 3 or len(cleaned) > 50:
        raise ValidationError("Username must be between 3 and 50 characters.")
    if not all(char.isalnum() or char in {"_", "-", "."} for char in cleaned):
        raise ValidationError("Username may contain letters, numbers, dot, dash, and underscore only.")
    return cleaned


def _validate_password(password: str) -> None:
    if len(password or "") < 10:
        raise ValidationError("Password must contain at least 10 characters.")
    if not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
        raise ValidationError("Password must include at least one letter and one number.")


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    _validate_password(password)
    salt_bytes = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, PBKDF2_ITERATIONS)
    return base64.b64encode(digest).decode("ascii"), base64.b64encode(salt_bytes).decode("ascii")


def verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    try:
        salt = base64.b64decode(stored_salt.encode("ascii"))
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
        )
        expected = base64.b64decode(stored_hash.encode("ascii"))
        return hmac.compare_digest(candidate, expected)
    except (ValueError, TypeError):
        return False


def user_count(path: str | Path | None = None) -> int:
    row = fetch_one("SELECT COUNT(*) AS total FROM auth_users", path=path)
    return int(row["total"] if row else 0)


def create_first_admin(username: str, password: str, path: str | Path | None = None) -> AuthenticatedUser:
    cleaned = _validate_username(username)
    password_hash, salt = hash_password(password)
    with transaction(path) as conn:
        if conn.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0] > 0:
            raise ConflictError("First-admin setup is already complete.")
        cursor = conn.execute(
            """
            INSERT INTO auth_users
            (username, password_hash, password_salt, role, is_active, created_at)
            VALUES (?, ?, ?, 'Admin', 1, ?)
            """,
            (cleaned, password_hash, salt, utc_now()),
        )
        user_id = int(cursor.lastrowid)
        actor = Actor(cleaned, "Admin")
        write_audit_log(
            conn, actor, "create", "auth_user", user_id,
            "First local administrator account created.",
            after_state={"id": user_id, "username": cleaned, "role": "Admin", "is_active": 1},
        )
    return AuthenticatedUser(user_id, cleaned, "Admin")


def create_user(
    username: str,
    password: str,
    role: str,
    actor: Actor,
    path: str | Path | None = None,
) -> int:
    if actor.role != "Admin":
        raise ValidationError("Only an administrator can create users.")
    cleaned = _validate_username(username)
    if role not in ROLE_PERMISSIONS:
        raise ValidationError("Role must be Admin or Staff.")
    password_hash, salt = hash_password(password)
    try:
        with transaction(path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO auth_users
                (username, password_hash, password_salt, role, is_active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (cleaned, password_hash, salt, role, utc_now()),
            )
            user_id = int(cursor.lastrowid)
            write_audit_log(
                conn, actor, "create", "auth_user", user_id,
                f"Local {role} account {cleaned} created.",
                after_state={"id": user_id, "username": cleaned, "role": role, "is_active": 1},
            )
            return user_id
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise ConflictError("That username already exists.") from exc
        raise


def authenticate_user(username: str, password: str, path: str | Path | None = None) -> AuthenticatedUser | None:
    cleaned = (username or "").strip()
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT * FROM auth_users WHERE username = ? COLLATE NOCASE", (cleaned,)
        ).fetchone()
        if row and row["is_active"] and verify_password(password or "", row["password_hash"], row["password_salt"]):
            conn.execute("UPDATE auth_users SET last_login_at = ? WHERE id = ?", (utc_now(), row["id"]))
            actor = Actor(row["username"], row["role"])
            write_audit_log(
                conn, actor, "login", "session", row["id"], "Local application login succeeded."
            )
            return AuthenticatedUser(int(row["id"]), row["username"], row["role"])

        failure_actor = Actor(cleaned or "unknown", "Unknown")
        write_audit_log(
            conn, failure_actor, "login_failed", "session", None,
            "Local application login failed. No password or secret was recorded.",
        )
    return None


def log_logout(user: AuthenticatedUser, path: str | Path | None = None) -> None:
    with transaction(path) as conn:
        write_audit_log(
            conn, user.actor, "logout", "session", user.id, "Local application logout."
        )


def can(user: AuthenticatedUser, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(user.role, set())


def list_users(path: str | Path | None = None):
    from database import fetch_df

    return fetch_df(
        """
        SELECT id, username, role, is_active, created_at, last_login_at
        FROM auth_users ORDER BY username
        """,
        path=path,
    )


def set_user_active(
    user_id: int,
    active: bool,
    actor: Actor,
    path: str | Path | None = None,
) -> None:
    if actor.role != "Admin":
        raise ValidationError("Only an administrator can manage users.")
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT id, username, role, is_active FROM auth_users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise ValidationError("User was not found.")
        if row["username"].lower() == actor.username.lower() and not active:
            raise ValidationError("You cannot disable your own active session.")
        before = dict(row)
        conn.execute("UPDATE auth_users SET is_active = ? WHERE id = ?", (1 if active else 0, user_id))
        after = {**before, "is_active": 1 if active else 0}
        write_audit_log(
            conn, actor, "update", "auth_user", user_id,
            f"User {row['username']} {'enabled' if active else 'disabled'}.", before, after,
        )
