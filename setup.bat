@echo off
:: setup.bat — One-time setup for Job Hunter on Windows
:: Run this once before using the pipeline:
::   setup.bat
:: Then use run.bat to launch the pipeline.

setlocal enabledelayedexpansion
title Job Hunter — Setup

echo.
echo ============================================================
echo   Job Hunter — Windows Setup
echo ============================================================
echo.

:: ── 1. Check Python 3.10+ ────────────────────────────────────────────────────
echo [1/7] Checking Python...
set PYTHON_CMD=
for %%C in (python python3) do (
    if "!PYTHON_CMD!"=="" (
        where %%C >nul 2>&1 && (
            for /f "tokens=2" %%V in ('%%C --version 2^>^&1') do (
                for /f "tokens=1,2 delims=." %%M in ("%%V") do (
                    if %%M geq 3 if %%N geq 10 set PYTHON_CMD=%%C
                )
            )
        )
    )
)

if "!PYTHON_CMD!"=="" (
    echo   Python 3.10+ not found.
    echo   Download from: https://www.python.org/downloads/
    echo   Make sure to check "Add Python to PATH" during install.
    echo   Then re-run this script.
    pause
    exit /b 1
)

for /f "tokens=2" %%V in ('!PYTHON_CMD! --version 2^>^&1') do echo   Found Python %%V

:: ── 2. Create virtual environment ────────────────────────────────────────────
echo.
echo [2/7] Setting up virtual environment...
if not exist venv\ (
    !PYTHON_CMD! -m venv venv
    echo   Virtual environment created.
) else (
    echo   Virtual environment already exists.
)

:: ── 3. Install Python packages ────────────────────────────────────────────────
echo.
echo [3/7] Installing Python packages...
call venv\Scripts\activate.bat
pip install --upgrade pip -q
pip install -r requirements.txt -q
if errorlevel 1 (
    echo   ERROR: pip install failed. Check requirements.txt and your internet connection.
    pause
    exit /b 1
)
echo   Python packages installed.

:: ── 4. Install Playwright browser ────────────────────────────────────────────
echo.
echo [4/7] Installing Playwright browser (Chromium)...
python -m playwright install chromium
if errorlevel 1 (
    echo   WARNING: Playwright browser install failed. You can retry later:
    echo     venv\Scripts\activate && python -m playwright install chromium
) else (
    echo   Playwright Chromium installed.
)

:: ── 5. Check Node.js ─────────────────────────────────────────────────────────
echo.
echo [5/7] Checking Node.js...
where node >nul 2>&1
if errorlevel 1 (
    echo   Node.js not found.
    echo   Download from: https://nodejs.org/  (LTS version recommended)
    echo   After installing Node.js, re-run this script.
    pause
    exit /b 1
) else (
    for /f %%V in ('node --version') do echo   Found Node.js %%V
)

:: ── 6. Install Claude Code CLI ───────────────────────────────────────────────
echo.
echo [6/7] Checking Claude Code CLI...
where claude >nul 2>&1
if errorlevel 1 (
    echo   Installing Claude Code CLI...
    npm install -g @anthropic-ai/claude-code
    if errorlevel 1 (
        echo   ERROR: npm install failed. Make sure Node.js is installed correctly.
        pause
        exit /b 1
    )
    echo   Claude Code CLI installed.
) else (
    echo   Claude Code CLI already installed.
)

:: ── 7. Claude login ───────────────────────────────────────────────────────────
echo.
echo [7/7] Claude login
echo.
echo   You need to log in with your Claude Pro account.
echo   A browser will open — sign in and approve the connection.
echo.
claude auth login
if errorlevel 1 (
    echo   Login skipped or failed. Run manually later: claude auth login
)

:: ── Create run.bat helper ─────────────────────────────────────────────────────
echo.
echo Creating run.bat helper...
(
echo @echo off
echo :: run.bat — Activates the virtual environment and runs the pipeline.
echo :: Usage: run.bat [main.py arguments]
echo :: Examples:
echo ::   run.bat --dry-run
echo ::   run.bat --start-from 2
echo ::   run.bat login_linkedin.py
echo call "%~dp0venv\Scripts\activate.bat"
echo set FIRST_ARG=%%1
echo if "%%FIRST_ARG:~-3%%"==".py" ^(
echo     python %%*
echo ^) else ^(
echo     python main.py %%*
echo ^)
) > run.bat
echo   run.bat created.

:: ── Done ─────────────────────────────────────────────────────────────────────
echo.
echo ============================================================
echo   Setup complete!
echo ============================================================
echo.
echo   Next steps:
echo   1. Copy config and fill in your details:
echo         copy config.example.yaml config.yaml
echo         notepad config.yaml
echo      Set your resume paths, name, email, phone, job titles.
echo.
echo   2. Save your LinkedIn session (one-time):
echo         run.bat login_linkedin.py
echo.
echo   3. Test with dry-run (no form submissions):
echo         run.bat --dry-run
echo.
echo   4. Run the full pipeline:
echo         run.bat
echo.
pause
endlocal
