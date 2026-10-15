#!/usr/bin/env bash
# One-command update for the EC2 server:  ~/amirclaw/scripts/update.sh
# Pulls main, installs/builds only what changed, restarts the bot only when needed,
# then checks the bot is online and the report engine runs.
set -euo pipefail

cd "$(dirname "$0")/.."
APP=amirclaw
PY=.venv/bin/python3

before=$(git rev-parse HEAD)
git pull --ff-only
after=$(git rev-parse HEAD)

if [ "$before" = "$after" ]; then
    echo "✅ Already up to date ($(git log --oneline -1))"
else
    changed=$(git diff --name-only "$before" "$after")
    echo "📥 Updated $(git rev-parse --short "$before") -> $(git rev-parse --short "$after")"
    echo "$changed" | sed 's/^/   /'

    restart=0
    if echo "$changed" | grep -qE '^package(-lock)?\.json$'; then
        echo "📦 Installing Node dependencies..."
        npm install --no-audit --no-fund
        restart=1
    fi
    if echo "$changed" | grep -q '^requirements\.txt$'; then
        echo "🐍 Installing Python dependencies..."
        $PY -m pip install -q -r requirements.txt
    fi
    if echo "$changed" | grep -qE '^(src/|tsconfig\.json$|package(-lock)?\.json$)'; then
        echo "🔨 Building TypeScript..."
        npm run build
        restart=1
    fi

    if [ "$restart" = 1 ]; then
        echo "🔄 Restarting $APP..."
        pm2 restart "$APP" --update-env >/dev/null
        sleep 8
    else
        echo "ℹ️  Python/config only: no restart needed (each report starts a fresh Python process)."
    fi
fi

# One-time: import the Excel history into the SQLite message store. Until this has run,
# reports keep reading the Excel file, so it is safe to run late or re-run.
if ! $PY -c "import sys; from reports_engine import messages_db as m; sys.exit(0 if m.is_ready(m.db_path_for(m.EXCEL_FILE)) else 1)"; then
    echo "🗄️  Importing WhatsApp Excel history into SQLite (first run)..."
    $PY -m reports_engine.messages_db
fi
$PY -m reports_engine.messages_db --fill-ts >/dev/null  # exact times for rows stored before the column existed

# Health checks
status=$(pm2 jlist | $PY -c "import sys,json; a=[p for p in json.load(sys.stdin) if p['name']=='$APP']; print(a[0]['pm2_env']['status'] if a else 'missing')")
if [ "$status" != "online" ]; then
    echo "❌ $APP is $status — check: pm2 logs $APP --lines 50 --nostream"
    exit 1
fi
today=$(TZ=Asia/Dubai date +%d-%m-%Y)
if $PY -m reports_engine.cli --report violations --date "$today" | tail -1 | $PY -c "import sys,json; json.load(sys.stdin)['text']" >/dev/null 2>&1; then
    echo "✅ $APP online, report engine OK ($(git log --oneline -1))"
else
    echo "❌ Report engine failed — run: $PY -m reports_engine.cli --report violations --date $today"
    exit 1
fi
