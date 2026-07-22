# Security Notes

Al Sadi Knitwear Factory OS v1.1 uses local application authentication for a trusted factory environment.

- Passwords use salted PBKDF2-HMAC-SHA256 with 600,000 iterations.
- Passwords, session tokens, and secrets are never written to audit logs.
- Roles are enforced in navigation and administrative services.
- SQLite foreign keys are enabled on every application connection.
- Delete and restore operations require confirmation; employees are archived to retain attendance.
- Restore accepts only integrity-checked files from the configured backups directory.
- Excel protection prevents accidental edits; it is not encryption or access control.

This release does not provide TLS termination, centralized identity, MFA, remote revocation, or enterprise audit retention. Do not expose Streamlit directly to the public internet. Use host/network access controls and operating-system file permissions.

Microsoft Graph, WhatsApp, and AI message parsing are intentionally outside v1.1 scope.
