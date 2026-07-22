# Telegram Bot Setup

Al Sadi Knitwear v1.2 uses local long polling. The Telegram service is separate from Streamlit and uses the same SQLite database through validated application services.

## Create The Bot With BotFather

1. Open the verified `@BotFather` account in Telegram and send `/newbot`.
2. Choose a display name and a unique username ending in `bot`.
3. Store the token in a password manager. Do not paste it into source code, chat messages, screenshots, or Git.
4. Use BotFather `/setjoingroups` to disable groups unless an approved private group is required.
5. Keep BotFather privacy mode enabled for any approved group.

## Configure The Token

For the current PowerShell session:

```powershell
$env:TELEGRAM_BOT_TOKEN="token-from-BotFather"
```

Alternatively, create the ignored `.streamlit/secrets.toml`:

```toml
[telegram]
bot_token = "token-from-BotFather"
# approved_group_id = -1001234567890
```

Never put a real token in either example file.

## Start Local Polling

```powershell
cd "E:\al sadi"
.\run_telegram_bot.cmd
```

Only one polling process can hold the SQLite heartbeat lease. Streamlit runs independently when the bot is stopped.

## Find And Authorize Numeric User IDs

1. Start the bot and send `/start` from the intended Telegram account.
2. The bot rejects the unknown account without returning factory information.
3. Sign in to Streamlit as Admin and open **Settings > Telegram Automation > Rejected**.
4. Read the ID from the `telegram:<numeric_id>` audit username.
5. Open **Authorized Users**, enter that numeric ID, choose Staff or Admin, and authorize it.

Usernames are never used for authorization because they can change.

## Approved Private Group

Private chats are allowed for authorized users. Group or supergroup messages are rejected unless the exact numeric chat ID is configured as `TELEGRAM_APPROVED_GROUP_ID` or `telegram.approved_group_id`. Keep that group private and control membership.

## Token Rotation

1. Stop `telegram_bot.py`.
2. Use BotFather `/revoke` and obtain the replacement token.
3. Replace the environment or secrets value.
4. Restart polling and use **Test Bot Connection**.
5. Confirm the old token no longer works.

## Emergency Disable

1. Stop the Telegram bot process.
2. Revoke the token with BotFather.
3. Remove the token from the environment or `.streamlit/secrets.toml`.
4. Set Telegram users to Suspended or Removed in Streamlit.
5. Review rejected, pending, and audit entries, then create a database backup.

## Backup And Recovery

Telegram users, pending operations, receipts, rate limits, and audit logs live inside `factory.db`; normal Data Safety backups include them. Restore only an integrity-checked backup and restart the bot. Pending operations older than 20 minutes expire without changing factory records.

## Future Webhooks

A hosted deployment could replace polling with a TLS webhook, secret webhook validation, a trusted reverse proxy, and managed process supervision. Webhooks are intentionally not implemented in v1.2.
