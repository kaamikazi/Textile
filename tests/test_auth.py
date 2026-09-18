from __future__ import annotations

import database
from auth import AuthenticatedUser, authenticate_user, can, create_first_admin, create_user


def test_role_permissions():
    admin = AuthenticatedUser(1, "admin", "Admin")
    staff = AuthenticatedUser(2, "staff", "Staff")
    assert can(admin, "restore_backups")
    assert can(admin, "manage_records")
    assert not can(staff, "restore_backups")
    assert not can(staff, "manage_records")
    assert can(staff, "add_production")
    assert can(staff, "add_expense")
    assert can(staff, "add_attendance")


def test_password_hash_and_audit_do_not_store_secret(tmp_path):
    path = tmp_path / "auth.db"
    database.initialize_database(path)
    secret = "StrongPassword123"
    admin = create_first_admin("factory_admin", secret, path)
    create_user("factory_staff", "AnotherPassword456", "Staff", admin.actor, path)
    assert authenticate_user("factory_admin", secret, path) is not None
    assert authenticate_user("factory_admin", "wrong-password", path) is None

    with database.connect(path) as conn:
        row = conn.execute("SELECT password_hash, password_salt FROM auth_users WHERE username = ?", ("factory_admin",)).fetchone()
        audit_text = " ".join(
            str(value or "")
            for log in conn.execute("SELECT * FROM audit_logs")
            for value in log
        )
    assert row["password_hash"] != secret
    assert row["password_salt"] != secret
    assert secret not in audit_text
    assert "AnotherPassword456" not in audit_text
