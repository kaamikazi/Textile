@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Same interpreter resolution as run_app.cmd: no hardcoded per-user
REM tooling cache path. The bot is a separate process from Streamlit and
REM is never started by it.
set "PY_CMD="
where py >nul 2>&1 && set "PY_CMD=py -3"
if not defined PY_CMD (
    where python >nul 2>&1 && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [ERROR] No Python interpreter found on PATH.
    echo Install Python 3.11+ and re-run, or see README.md.
    exit /b 9009
)

%PY_CMD% -c "import telegram, pandas, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Required packages are missing for this interpreter: %PY_CMD%
    echo Run: %PY_CMD% -m pip install -r requirements.txt
    exit /b 1
)

REM google-genai powers free-text intake (gemini_intake.py) and is
REM optional: without it the bot runs the button flow only, so warn
REM rather than refusing to start.
%PY_CMD% -c "import google.genai" >nul 2>&1
if errorlevel 1 (
    echo [WARN] google-genai is not installed; natural-language intake is disabled.
    echo Run: %PY_CMD% -m pip install -r requirements.txt
)

if not defined GEMINI_API_KEY (
    if not exist "%~dp0.streamlit\secrets.toml" (
        echo [INFO] No GEMINI_API_KEY set; the bot will use the button flow only.
    )
)

if not defined TELEGRAM_BOT_TOKEN (
    if not exist "%~dp0.streamlit\secrets.toml" (
        echo [ERROR] No Telegram bot token configured.
        echo Set TELEGRAM_BOT_TOKEN, or add [telegram] bot_token to .streamlit\secrets.toml.
        echo See TELEGRAM_SETUP.md.
        exit /b 1
    )
)

%PY_CMD% "%~dp0telegram_bot.py"
exit /b %errorlevel%
