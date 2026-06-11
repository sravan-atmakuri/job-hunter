#!/bin/bash
# launch_apply.sh — Kills ALL Chrome processes, relaunches with Profile 1 + debug port,
# then runs the applier connected to your real Chrome session.

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="Profile 1"
DEBUG_PORT=9222
CHROME_DATA="$HOME/Library/Application Support/Google/Chrome"

echo ""
echo "── Job Apply Launcher ──────────────────────────────"

# Step 1: Kill ALL Chrome-related processes aggressively
echo "Force-closing all Chrome processes..."
pkill -9 -f "Google Chrome" 2>/dev/null
pkill -9 -f "chrome_crashpad" 2>/dev/null
sleep 6

# Confirm no Chrome processes remain
REMAINING=$(pgrep -f "Google Chrome" 2>/dev/null | wc -l | tr -d ' ')
if [ "$REMAINING" -gt "0" ]; then
    echo "Chrome still running ($REMAINING processes) — waiting 5 more seconds..."
    sleep 5
    REMAINING=$(pgrep -f "Google Chrome" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$REMAINING" -gt "0" ]; then
        echo "ERROR: Could not kill all Chrome processes. Run manually:"
        echo "  sudo pkill -9 -f 'Google Chrome'"
        exit 1
    fi
fi
echo "  All Chrome processes stopped."

# Step 2: Remove ALL singleton/lock files that prevent a clean start
echo "Cleaning Chrome lock files..."
for PROFILE_DIR in "$CHROME_DATA/Default" "$CHROME_DATA/$PROFILE" "$CHROME_DATA/Profile 2"; do
    rm -f "$PROFILE_DIR/SingletonLock" 2>/dev/null
    rm -f "$PROFILE_DIR/SingletonSocket" 2>/dev/null
    rm -f "$PROFILE_DIR/SingletonCookie" 2>/dev/null
done
# Root-level Chrome singleton
rm -f "$CHROME_DATA/SingletonLock" 2>/dev/null
rm -f "$CHROME_DATA/SingletonSocket" 2>/dev/null

# Step 3: Launch Chrome fresh with remote debugging enabled
# --remote-allow-origins=* is required in Chrome 96+ to allow CDP connections
echo "Launching Chrome with Profile '$PROFILE' on debug port $DEBUG_PORT..."
"$CHROME" \
  --remote-debugging-port=$DEBUG_PORT \
  --remote-allow-origins="*" \
  --profile-directory="$PROFILE" \
  --no-first-run \
  --no-default-browser-check \
  --disable-background-networking \
  --disable-component-update \
  > /tmp/chrome_debug.log 2>&1 &

CHROME_PID=$!
echo "Chrome PID: $CHROME_PID"

# Step 4: Wait for the debug port (up to 60 seconds)
echo "Waiting for Chrome debug port (up to 60 seconds)..."
SUCCESS=0
for i in {1..30}; do
    sleep 2
    if curl -s --max-time 2 http://127.0.0.1:$DEBUG_PORT/json/version > /dev/null 2>&1; then
        echo "✓ Chrome ready on port $DEBUG_PORT"
        SUCCESS=1
        break
    fi
    # Check if Chrome process died
    if ! kill -0 $CHROME_PID 2>/dev/null; then
        echo "ERROR: Chrome process died. Log:"
        cat /tmp/chrome_debug.log
        exit 1
    fi
    echo "  waiting... ($i/30)"
done

if [ "$SUCCESS" -ne "1" ]; then
    echo ""
    echo "ERROR: Chrome did not open the debug port after 60 seconds."
    echo ""
    echo "Chrome log:"
    cat /tmp/chrome_debug.log
    echo ""
    echo "Chrome IS running (PID $CHROME_PID) but the debug port is not responding."
    echo "This can happen with Chrome 126+ — trying fallback mode..."
    echo ""
    echo "The apply script will fall back to Playwright's Chromium + saved LinkedIn session."
    echo "Make sure you ran: python3 login_linkedin.py  (to save your LinkedIn session)"
fi

echo ""
if [ "$SUCCESS" -eq "1" ]; then
    echo "Chrome is open with your Sravan profile."
    echo "Make sure you are logged into LinkedIn in the Chrome window."
fi
echo "Press Enter when ready to start applying..."
read -r

echo ""
echo "Starting application step..."
# Activate venv so Playwright and all dependencies are available
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
else
    echo "WARNING: venv not found at $SCRIPT_DIR/venv — run 'bash setup.sh' first"
fi
python3 main.py --start-from 4
