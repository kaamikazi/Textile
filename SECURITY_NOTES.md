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

Microsoft Graph and WhatsApp are intentionally outside scope.

Optional natural-language Telegram intake (`gemini_intake.py`) sends the text
of a staff message to Google's Gemini API for parsing when `GEMINI_API_KEY` is
configured. It is off by default and the bot works without it.

- Only the message text leaves the machine. No database contents, employee
  records, credentials or configuration are sent.
- Parsing never writes to SQLite. It produces a pending operation that must be
  confirmed on the same Confirm/Edit/Cancel screen as any other entry, so the
  write still happens in one validated transaction with an audit entry.
- Authorization, chat restriction and rate limiting are applied before a
  message is parsed, so free text cannot reach the API if a command from the
  same user would have been refused.
- An extracted machine is checked against the machines table and refused if it
  is unregistered or ambiguous; missing numbers are refused, never inferred.
- Entries created this way are tagged `[natural-language]` in the audit log.
- The API key is read only from `GEMINI_API_KEY` or Streamlit secrets, is never
  logged, and is never shown in a chat.

Telegram v1.2 uses a separate local polling process, numeric-ID allowlisting, confirmation gates, persistent rate limiting, idempotency receipts, and token-redacted logs. Telegram is not an enterprise identity provider and does not make the Streamlit service suitable for direct public-internet exposure. See `TELEGRAM_SECURITY.md`.
