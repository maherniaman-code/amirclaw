# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

amirClaw is one Node/TypeScript process (pm2 app `amirclaw` on an AWS EC2 box) that does three jobs:

1. **Owner's Telegram assistant** (`src/index.ts`, grammy): chat with LLM providers (`src/llm.ts`:
   OpenRouter, NVIDIA, Gemini, AgentRouter, Groq; `PRIMARY_PROVIDER` picks the first), tools in
   `src/tools/`, reminders, TTS. Only `ALLOWED_USER_IDS` pass the auth middleware.
2. **Read-only WhatsApp capture** (`src/whatsapp/client.ts`, Baileys): records every group message
   of the linked number into `workspace/whatsapp_messages.db` (+ Excel mirror, Firestore mirror).
   An optional second number (`WHATSAPP2_GROUPS`) records only the groups listed there.
3. **Meydan facility reports**: a Python engine (`reports_engine/`) classifies those messages into
   parking, maintenance, fire & safety, operations, violations, vehicle permissions and cleaning
   reports. TypeScript calls it as a subprocess and sends the results on Telegram.

## Commands

```bash
npm run dev                      # run the bot locally with tsx watch (needs .env)
npm run build                    # tsc -> dist/ (production runs dist/index.js under pm2)
npx tsc --noEmit                 # type check

python3 -m pytest tests -q                                   # Python tests
python3 -m pytest tests/test_reports_engine.py -k Undo -q    # one test / class
npx tsx tests/test_corrections.ts                            # one TS test (each file is a script)
UPDATE_FIXTURES=1 python3 -m pytest tests/test_fixture_reports.py   # regenerate golden outputs, then review the diff

python3 -m reports_engine.cli --report maintenance --date 04-11-2026 --shift Day   # any report as JSON
python3 -m reports_engine.cli --report parking --slot 10:00 --date 04-11-2026
python3 -m reports_engine.evaluate --save before.json        # snapshot every message's decision
python3 -m reports_engine.evaluate --diff before.json        # what a rule change altered
python3 -m reports_engine.reprocess [--apply]                # rebuild tickets/warehouse with current rules
```

On macOS use the framework Python (`/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`)
if Homebrew's `python3` fails on expat; `tests/test_bridge.ts` reads the interpreter from `PYTHON_PATH`.
CI (`.github/workflows/tests.yml`) runs pytest, `tsc --noEmit` and a fixed list of TS tests.

Deploy = the one script on the server, after merging to `main`:
```bash
ssh -i ~/Downloads/amirclaw-key.pem ubuntu@16.171.33.170
~/amirclaw/scripts/update.sh          # pull, npm install/build/restart only if needed, reprocess if rules changed, health check
```

## Architecture

**TS -> Python bridge.** `src/reports/bridge.ts` `runReportEngine()` spawns
`python3 -m reports_engine.cli --report <type> ...` and parses the last JSON line of stdout
(`text`, `excel_path`, `diagnostics`, `items`, `missing`, ...). Each report is a fresh process, so
Python/YAML-only changes need no restart. New CLI fields must also be mapped in `bridge.ts`.

**Rules live in data, not code.** `config/meydan_rules.yaml` holds group names, capacities, slot
times, and every keyword list (`fault`, `closure`, `closure_exact`, `followup_not_ticket`,
`in_progress`, `operations`, `vehicle_permission`, cleaning lists...). Matching semantics differ by
list: fault keywords are substrings; operations vendor/activity words match at a word start;
`closure_exact` is a whole-message match after normalization; closures are ignored when negated or
asked. Change rules with the evaluate save/diff workflow (project skill `update-keywords` in
`.claude/skills/`), review every changed decision, and add a scenario test.

**Engine flow** (`reports_engine/manager.py` `ReportsManager`): `whatsapp_parser.py` loads messages
(SQLite store once `meta.excel_backfill_done` is set, else the Excel workbook; `REPORTS_SOURCE`
forces one) and groups photo+caption events -> `generate_<report>()` classifies each event as a new
ticket / closure / progress / follow-up / ack and links replies to tickets (`_link_reply`: quoted
reply first, then shared content word within `reply_link_hours`, then nearest open ticket within
`reply_link_minutes`) -> owner Telegram corrections (`corrections_db.py`) override the rules ->
`report_generators.py` renders text, `excel_exporter.py` the workbook. `tickets_db.py` persists
ticket status across shifts (carry-over), `warehouse.py` records each run for audit
(`--explain <msgid>`), `reprocess.py` rebuilds both from all messages (60 days) when rules change.
The parking report also reads building figures from an IMAP email (`email_reader.py`), falling back
to WhatsApp posts; podium/Rumble posts are judged by the time written in them.

**Scheduling** (`src/reports/scheduler.ts`, `parking_drafts.ts`): team-facing shift and parking
reports run only with `ENABLE_AUTOMATED_REPORTS=true` (kept off). Owner-only messages have their own
flags and go to `ALLOWED_USER_IDS`: 07:00 vehicles allowed today, 08:00 digest, hourly overdue
reminders, 05:00 data-quality metrics, weekly quality/KPI/keyword-review, and the 10/12/14 parking
report with a "Send to Control Room" button. `sent_reports_log` in `workspace/reports.db` prevents
duplicates across restarts.

**WhatsApp stays read-only.** The only send path is `sendTextToGroup()` in `client.ts`, called only
when the owner presses the parking "Send to Control Room" button. Do not add others.

**State** is all under `workspace/` (gitignored): WhatsApp sessions, `whatsapp_messages.db/.xlsx`,
`tickets.db`, `reports_corrections.db`, `reports_warehouse.db`, `reports.db`. Tests never touch it:
`tests/conftest.py` freezes `REPORTS_NOW=2026-10-15T12:00+04:00` and points every DB env var
(`TICKETS_DB_PATH`, `REPORTS_DB_PATH`, `WAREHOUSE_DB_PATH`) to a temp folder; tests that need live
data use `@requires_live_data` and skip in CI, while `tests/fixtures/` holds an anonymized fixture
with golden outputs. Use `now_dubai()` (honours `REPORTS_NOW`) instead of `datetime.now()`.
A maintenance "Full" day runs 05:00 -> 05:00; Day shift 05:00-17:00, Night 17:00-05:00 (Asia/Dubai).

## Owner rules that the code must keep

- **No Arabic in anything the bot sends** (reports, buttons, alerts); `tests/test_english_only.ts`
  enforces it. Arabic is only for talking to the owner in chat.
- SLA breaches are tracked in the backend only, never shown in report text.
- Guards' fault messages in the Security Team group go to Needs Review, not operations and not
  maintenance (the same fault is posted in the FM group).
- Routine vendor work (watering, garbage removal, cleaning prep) is an operations activity.
- Reports print names without phone numbers; HR/leave messages never go to Firebase.
- The bot's own finished parking report ("MEYDAN PARKING") is never read back as a source.
- Never commit `.env`, `service-account.json`, `workspace/`, `backups/`, `*.db`, session files.
- Merge PRs only after the owner explicitly says so ("ادمج"); PRs are squash-merged.
