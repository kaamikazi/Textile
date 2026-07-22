# Telegram Security

## Trust Boundaries

- SQLite remains authoritative. Telegram never writes directly to Excel.
- Numeric Telegram user IDs are authorization keys. Usernames are untrusted display data.
- Only Streamlit Admin users can authorize, suspend, reactivate, or remove Telegram access.
- Private chats are allowed; a group is accepted only when its exact private numeric ID is configured.
- Telegram authentication does not replace operating-system, network, or Streamlit security.

## Mutation Controls

- Workflows are deterministic, button-guided, and field-by-field.
- Every value is validated again on the server at confirmation time.
- All SQLite statements are parameterized.
- Confirmation is mandatory; free-form or generated text cannot save directly.
- Each operation has a UUID and unique idempotency receipt. Repeated confirmation returns the original result.
- The mutation, activity, audit entry, receipt, and confirmed state commit in one transaction.
- Excel synchronization runs only after commit. SQLite remains saved when Excel is locked or unavailable.
- Incomplete operations expire after 20 minutes and are audited without creating factory records.

## Abuse And Service Controls

- Persistent rate limits temporarily block excessive requests.
- A heartbeat lease prevents duplicate polling instances.
- The token is read only from `TELEGRAM_BOT_TOKEN` or ignored Streamlit secrets.
- Structured logs redact the configured token across application and HTTP loggers.
- Rejected accounts receive no factory information.
- Salaries, phone numbers, database paths, tokens, and configuration are excluded from Telegram summaries.

## Incident Response

Stop polling, revoke the token in BotFather, suspend Telegram users, review audit logs, and create a database backup. Detailed rotation and emergency-disable steps are in `TELEGRAM_SETUP.md`.

This local polling release does not include webhooks, generative AI, natural-language parsing, end-to-end Telegram message encryption, enterprise identity, or public-internet hardening.
