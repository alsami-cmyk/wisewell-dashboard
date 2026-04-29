#!/bin/bash
# Runs the Meta Ads sync. Invoked by cron twice daily.
# Logs to /Users/sami/Desktop/Claude Code/logs/meta_sync.log

set -euo pipefail

PROJECT="/Users/sami/Desktop/Claude Code"
LOG="$PROJECT/logs/meta_sync.log"
PYTHON="/Library/Developer/CommandLineTools/usr/bin/python3"

mkdir -p "$PROJECT/logs"

echo "──────────────────────────────────" >> "$LOG"
echo "$(date '+%Y-%m-%d %H:%M:%S') Starting Meta sync" >> "$LOG"

# Load .env
set -a
source "$PROJECT/.env"
set +a

# Load GOOGLE_SERVICE_ACCOUNT from secrets.toml
export GOOGLE_SERVICE_ACCOUNT=$("$PYTHON" -c "
import toml, json, sys
try:
    s = toml.load('$PROJECT/.streamlit/secrets.toml')
    sa = s['GOOGLE_SERVICE_ACCOUNT']
    print(json.dumps(sa) if isinstance(sa, dict) else sa)
except Exception as e:
    print('ERROR: ' + str(e), file=sys.stderr)
    sys.exit(1)
")

cd "$PROJECT"
"$PYTHON" "$PROJECT/scripts/sync_marketing_spend.py" >> "$LOG" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') Done" >> "$LOG"
