---
name: update-keywords
description: Update the amirClaw report rules (config/meydan_rules.yaml keywords, closures, vendors, follow-ups) from the latest WhatsApp messages - pull live data from the EC2 server, snapshot every decision, review what the bot got wrong, edit the rules, review every changed decision, add scenario tests, run all tests and open a PR. Use when the owner asks to update / refresh the keywords ("حدث الكلمات المفتاحية"), fix messages the reports missed or got wrong, or after the weekly keyword-review list.
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# Update keywords

## Goal
The maintenance, fire & safety, operations, parking and cleaning reports classify WhatsApp
messages with the rules in `config/meydan_rules.yaml`. Every week or two new wording appears
(new vendors, new fault phrases, new ways of saying "done"). This skill finds what the rules got
wrong in a period, fixes the rules **without side effects on older messages**, proves it with
tests, and opens a PR. Nothing reaches the server until the owner says "ادمج".

## Inputs
- **Period** to review (DD-MM-YYYY to DD-MM-YYYY). Default: from the day after the last merged
  keyword PR (`gh pr list --state merged --search "keywords in:title" --limit 1`) up to today.
- Optional: the owner's notes ("this was not a ticket", "this vendor is missing").
- Access: SSH key `~/Downloads/amirclaw-key.pem`, server `ubuntu@16.171.33.170` (overridable with
  `AMIRCLAW_SSH_KEY` / `AMIRCLAW_HOST`).

## Scripts (in `scripts/`, call them with their full path)
| Script | Does |
|---|---|
| `pull_data.sh DEST` | Read-only backup copy of the server's messages, owner corrections and tickets into DEST |
| `setup_worktree.sh BRANCH DIR DATA` | Branch from origin/main in its own worktree, `workspace` -> DATA, `node_modules` linked (both git-excluded) |
| `evaluate.sh save\|diff SNAPSHOT.json` | Rules-only decision of every message; `diff` lists what changed and saves `SNAPSHOT.after.json`; both print accuracy vs owner corrections |
| `review_period.py --db --snapshot [--from --to] [--before]` | Messages with their decision for a period, or only the changed ones with full text |
| `run_tests.sh` | pytest, `tsc`, every `tests/test_*.ts`; exit 1 on any failure |

## Process
Use a scratch folder `S` (the session scratchpad) and run everything from the worktree `W`.
`SK` = this skill's folder (`<repo>/.claude/skills/update-keywords`).

1. **Data and branch**
   ```bash
   $SK/scripts/pull_data.sh $S/data
   $SK/scripts/setup_worktree.sh claude/keywords-<period> $S/kw $S/data
   cd $S/kw
   ```
2. **Snapshot before any edit**
   ```bash
   $SK/scripts/evaluate.sh save $S/before.json
   ```
   Note the accuracy line; the change must not lower it. It judges the last 120 days (~35 s);
   `EVAL_DAYS=0` judges the whole history (15+ minutes). Use the same value before and after.
3. **Review the period** (read every line, it is the whole point)
   ```bash
   python3 $SK/scripts/review_period.py --db workspace/whatsapp_messages.db --snapshot $S/before.json --from <D1> --to <D2> > $S/period.txt
   REPORTS_SOURCE=sqlite python3 -m reports_engine.cli --report review_candidates
   ```
   Look for: faults with "-" or `:excluded` (missed tickets); chatter that became tickets
   (`maintenance:open` on "please update", "we reported", greetings); fixes left open
   ("it's working", "know working", a photo reply); new vendor names in Security Team messages;
   parking requests not parsed. Write the list (message, wrong decision, right decision, why).
4. **Edit `config/meydan_rules.yaml`** - the narrowest rule that fixes the case:
   - `fault:` substring match -> use phrases ("making noise", "hole under"), never single short words.
   - `closure:` words not negated or asked; `closure_exact:` whole message only ("it's working").
   - `followup_not_ticket:` chasers and status questions ("still not rectified", "update and reply").
   - `operations:` vendor/activity lists match at word start; aliases for misspellings ("print avanue").
   - `not_activity:` operations messages that are not activities ("lost and found").
   - `vehicle_permission:` request phrases ("allow in podium").
   Keep the file's comment style: a short comment with the date and the real message.
5. **Diff and review every changed decision**
   ```bash
   $SK/scripts/evaluate.sh diff $S/before.json
   python3 $SK/scripts/review_period.py --db workspace/whatsapp_messages.db --snapshot $S/before.after.json --before $S/before.json
   ```
   Every change must be one you intended. An unintended one (an announcement becoming a ticket,
   an AC chaser becoming a fault) means the rule is too wide: narrow it and repeat step 5.
6. **Scenario tests** in `tests/test_reports_engine.py`: a new class `TestMessages<DDMon><DDMon>`
   modelled on `TestMessages26Oct2Nov` (the `_items` helper and `_wa_msg`, October 2026 dates, the
   real wording). One row per fixed case, plus a row proving a near-miss stays unchanged.
7. **Run everything**
   ```bash
   $SK/scripts/run_tests.sh
   ```
8. **Commit and PR** (only the yaml, tests and any code you changed; never `workspace/`, `.env`,
   session files). Commit message ends with the Co-Authored-By line from the session reminder;
   PR body: period, table of fixed cases (message -> before -> after), accuracy before/after,
   number of changed decisions, tests run, and the Claude Code footer.
9. **Tell the owner in plain Arabic**: what was wrong, what changed, the PR link, then wait for
   "ادمج". After merging: `git pull` in the main checkout, `git worktree remove $S/kw`, and give
   the deploy commands (the owner runs them, or Claude does when asked):
   ```bash
   ssh -i ~/Downloads/amirclaw-key.pem ubuntu@16.171.33.170
   ~/amirclaw/scripts/update.sh
   ```
   `update.sh` reprocesses old messages with the new rules (stores backed up first) and prints
   the tickets that changed; check that list - tickets older than 60 days drop out by design.

## Outputs
- One PR with the rule changes and scenario tests, all checks passing.
- A short Arabic summary for the owner: wrong cases found, fixes, changed-decision count, PR link.

## Edge Cases
- **Owner corrections are ground truth but can be mistakes**: on 25-10 the owner tapped ❌ on
  routine vendor work meaning "not a maintenance ticket"; routine vendor work IS an operations
  activity. Ask before turning a correction into a rule that removes a whole kind of item.
- **Guards' fault messages in the Security Team group** stay out of operations and maintenance
  (the same fault is posted in the FM group).
- **No Arabic in anything the bot sends** (reports, buttons, alerts); `tests/test_english_only.ts` enforces it.
- **The bot's own finished reports** ("MEYDAN PARKING") are never a source.
- Mac: Homebrew `python3` may have a broken expat - use the framework Python
  (`/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`, the scripts default to it,
  override with `PYTHON`). There is no `timeout` command on macOS.
- Evaluating on the server is too slow (t3.micro); always pull the data and run locally.
- Create the `workspace` link (setup_worktree.sh) before running any test: pytest creates a real
  `workspace/` folder when it is missing, and `ln -s` then puts the link inside it.
- `diff` shows at most 50 ids; `review_period.py --before` shows them all - read them all.
- A PR that cannot merge after another was squash-merged: rebase onto origin/main, push with
  `--force-with-lease`.
- Merging needs the owner's explicit "ادمج" in chat; never merge on your own.

## Environment
No API keys. Needs: SSH key above, `gh` logged in to `maherniaman-code/amirclaw`, `sqlite3`,
Python 3.10+ with the project requirements, Node 18+ with `npm install` done in the main checkout.

## Learned (update this list when something breaks)
- 13-11: tested end to end on live data: snapshot 35 s, a one-line test rule changed 145 decisions
  and review_period.py --before listed every one - exactly the review step 5 needs.
- 02-11: "prepar" as an activity word turned two announcements into activities - prefer whole phrases.
- 02-11: a ≤6-word fix quoting a message the bot never received closes the nearest open ticket;
  longer explanations quoting an unknown message do not.
