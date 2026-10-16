# Security review — 16 October 2026

Scope: this repository (Telegram bot, WhatsApp capture, reports engine, cloud backup, CI) by code
review, dependency audit and a secret scan of the git history. The EC2 server, AWS account and
Firebase project were **not** inspected; items about them are recommendations to check.

## How the system is exposed

| Entry point | Who can reach it | Trust |
|---|---|---|
| Telegram bot (long polling, no webhook or open port) | Anyone can message it; only `ALLOWED_USER_IDS` get past the middleware | Owner = trusted |
| WhatsApp groups (read-only, the bot never sends) | Every member of every group the linked phone is in | **Untrusted text and media** |
| Web pages, uploaded files, chat exports | Whoever wrote them | **Untrusted** |
| LLM providers (OpenRouter, Groq, Gemini, ...) | Receive chat history and tool results | Third party |
| Firebase backup | Anyone with the service account or project access | Holds the WhatsApp login |
| SSH to EC2 | Holder of `amirclaw-key.pem` | Owner |

Assets: Telegram bot token and API keys (`.env`), the WhatsApp login (`workspace/whatsapp_session`,
equivalent to the phone's account), message history including HR messages, tickets, the Firebase
service account.

## Risk register

| # | Risk | Severity | Likelihood | Status |
|---|---|---|---|---|
| 1 | **Prompt injection to code execution.** The assistant reads untrusted text (web pages, files, WhatsApp summaries) and could call `execute_python`, which ran immediately with the bot's full environment (bot token, API keys, IMAP password) and could read `.env` and the WhatsApp login and send them anywhere. | Critical | Medium | **Fixed**: every script is shown with Run / Cancel buttons and runs only when the same owner taps Run (once, within 10 minutes); the script gets no secrets in its environment. |
| 2 | **`read_file` could read the WhatsApp login and databases** (`whatsapp_session/creds.json`, `*.db`), and its folder check used a text prefix (`workspace_evil/` passed). | High | Medium | **Fixed**: proper path check; WhatsApp session, databases, backups, keys and `.env` files are refused. |
| 3 | **SSRF in `scrape_webpage`**: it fetched any URL, including `169.254.169.254` (EC2 instance credentials) and local services, also through redirects. | High | Low–Medium | **Fixed**: public addresses only, every redirect hop checked. |
| 4 | Uploaded file names were used as given (`../` in a name). | Low | Low | **Fixed**: base name only, no hidden files. |
| 5 | Python code still runs as the bot's user with network access once approved: a careless tap on Run can still leak files. | Medium | Low | Open: read the code before Run. Stronger option: run it as a separate Linux user without access to `~/amirclaw/.env` and the session folder. |
| 6 | WhatsApp login uploaded to Firestore unencrypted (Google encrypts at rest, but anyone with the service account or project access can take over the WhatsApp account). | Medium | Low | Open: encrypt the backup with a key kept only on the server; check who has access to the Firebase project and the service account. |
| 7 | Node.js 20 is past end of life (April 2026): no more security fixes. | Medium | Medium | Open: move the server to Node 22 or 24 LTS. |
| 8 | Ubuntu shows pending security updates. | Medium | Medium | Open: `sudo apt-get upgrade` and enable `unattended-upgrades`. |
| 9 | SSH: unknown whether port 22 is open to the whole internet. | Medium | Unknown | Check: limit the AWS security group to your IP; key-only login (password login off). |
| 10 | Logs hold message text and sender ids (`Captured WhatsApp group message ...`). | Low | — | Open: acceptable with pm2-logrotate; keep log retention short. |
| 11 | `.env` and `service-account.json` are readable by every local user on the laptop (`-rw-r--r--`). | Low | Low | Open: `chmod 600` on the laptop and the server. |
| 12 | npm audit: 2 moderate (`uuid` via `exceljs`); the vulnerable call is not used by the bot. | Low | Low | Accepted; recheck on upgrades. |

Checked and fine:
- **Telegram access control**: one middleware allows only `ALLOWED_USER_IDS` for every update (messages, buttons, commands). Long polling means no webhook to forge and no open port.
- **WhatsApp** is read-only: the bot never sends messages, so group members cannot use it to reach anyone.
- **SQL**: queries use parameters; the only formatted SQL uses fixed column names.
- **Secrets in git**: no `.env`, keys, session files or databases were ever committed (whole history scanned for token formats; counts only, nothing printed). `.env.example` holds placeholders.
- **Privacy**: reports and the messages Excel print no phone numbers; HR messages are not sent to the cloud.

## If a secret leaks (runbook)

1. Telegram token: BotFather → `/revoke`, put the new token in `.env` on the server, `pm2 restart amirclaw --update-env`.
2. LLM API keys / IMAP password: revoke in each provider's console, replace in `.env`, restart.
3. WhatsApp login: on the phone, WhatsApp → Linked devices → log out the bot; delete `workspace/whatsapp_session` on the server; scan a new QR with `/whatsapp`.
4. Firebase service account: Google Cloud console → IAM → service accounts → delete the key, create a new one.
5. SSH key: create a new key pair, add it to `~/.ssh/authorized_keys`, remove the old one.
6. Write down what leaked, when, and what was rotated.

Severity levels for incidents: **Critical** (login or token leaked, someone else using the bot or the
WhatsApp account), **High** (data exposed to an outsider), **Medium** (service down or data loss with
backup available), **Low** (no data or access affected).
