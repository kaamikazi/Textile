@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Resolve a Python that actually has the dependencies installed.
REM This used to hardcode a per-user tooling cache path, which stopped
REM working as soon as that cache was cleaned. Prefer the py launcher,
REM then python on PATH, and verify Streamlit is importable before
REM starting so a broken environment reports itself instead of failing
REM silently into the log.
set "PY_CMD="
where py >nul 2>&1 && set "PY_CMD=py -3"
if not defined PY_CMD (
    where python >nul 2>&1 && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [ERROR] No Python interpreter found on PATH.>> "%~dp0streamlit.log"
    echo Install Python 3.11+ and re-run, or see README.md.>> "%~dp0streamlit.log"
    exit /b 9009
)

%PY_CMD% -c "import streamlit, pandas, plotly, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Required packages are missing for this interpreter: %PY_CMD%>> "%~dp0streamlit.log"
    echo Run: %PY_CMD% -m pip install -r requirements.txt>> "%~dp0streamlit.log"
    exit /b 1
)

REM --server.address 0.0.0.0 makes the console reachable from other
REM machines on the network (factory tablets). See SECURITY_NOTES.md and
REM PRODUCTION_READINESS.md before changing this.
%PY_CMD% -m streamlit run "%~dp0app.py" --server.address 0.0.0.0 --server.port 8501 --server.headless true >> "%~dp0streamlit.log" 2>&1
exit /b %errorlevel%
