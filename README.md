# Personal Web Activity Tracker

A macOS background service that monitors your daily web browsing across all major browsers, uses **OpenAI** to generate an intelligent summary, and emails the digest to you (and optionally an accountability partner) on a configurable schedule — hourly, daily, weekly, or monthly — via **MailerSend**.

---

## 🎯 Purpose & Why This Exists

Most people don't realise how much time they spend on unproductive or harmful websites until they see it written down. This tool makes your browsing visible — to yourself, or to someone you trust.

### Personal productivity
Get a daily AI-generated digest of where your time actually went online. No vague estimates — real domains, real categories, real timeline. Use it to identify time sinks, stay accountable to yourself, and gradually shift your browsing habits.

### Accountability partnership
If you or someone you care about is working to overcome unhealthy browsing habits (excessive social media, adult content, gambling, etc.), this tool can serve as a lightweight, consent-based accountability system.

The person being monitored installs the tracker on their own machine with full knowledge. Their browsing digest is emailed to both themselves and a trusted accountability partner. The partner doesn't need to watch in real time — they simply review the daily digest together and have honest conversations.

> **Consent is everything.** This tool should only ever be installed by the person being monitored, with their full knowledge and agreement. Using it without someone's awareness is a violation of their privacy and trust — and likely illegal. This is a tool for mutual accountability, not surveillance.

---

## ✨ Features

- 🌐 **Multi-browser support** — tracks exact URLs from Safari, Chrome, Edge, Brave, Arc, Vivaldi (including private/incognito windows); falls back to window title for Firefox, Tor Browser, and Opera
- 🤖 **AI-powered digest** — OpenAI (`gpt-4o-mini` by default) categorises your activity, surfaces top domains, highlights notable browsing sessions, and **explicitly flags** any adult, gambling, or self-harm content
- 📧 **Email delivery** — sends a clean HTML email via MailerSend with Overview, Top Domains, Categories, Timeline Highlights, and Concern Flags sections
- ⏰ **Flexible schedule** — `hourly`, `daily`, `weekly`, or `monthly` digest controlled entirely by environment variables (powered by APScheduler)
- 🚀 **Runs as a macOS daemon** — both the tracker and summariser are managed by launchd: auto-start on login, auto-restart on crash
- 🛡️ **Tamper detection** — a SHA-256 checksum sidecar file is updated on every log write; the summariser alerts if the log has been modified between writes
- 👁️ **Watchdog** — the summariser checks every 5 minutes that the tracker service is still running, and sends an immediate alert if it has been stopped
- 🔐 **Privacy-preserving AI** — only domain names (not full URLs or paths) are sent to OpenAI; complete URLs remain on the local machine only
- 🔒 **Secrets via env vars** — no credentials ever hardcoded; all keys live in a `.env` file

---

## 📁 Project Structure

```
personal_tracker/
├── tracker.py                          # Browser polling daemon (AppleScript + Python)
├── summarizer.py                       # Long-running scheduler daemon (APScheduler + OpenAI)
├── config.py                           # Environment variable loader
├── requirements.txt                    # Python dependencies
├── com.tracker.web.plist               # launchd template — tracker service
├── com.tracker.summary.plist           # launchd template — summariser service
├── install.sh                          # One-command install script
├── uninstall.sh                        # One-command uninstall script
├── .env.template                       # Configuration template (copy to .env)
└── detailed_activity_log.txt.sha256    # Tamper-detection checksum (auto-generated)
```

---

## ✅ Prerequisites

Complete **all** of the following before running `install.sh`.

### 1. macOS with Python 3.8+

```bash
python3 --version   # must be 3.8 or higher
```

Install via [Homebrew](https://brew.sh) if needed:

```bash
brew install python
```

### 2. Accessibility & Automation permissions

The tracker uses AppleScript to read browser tabs — including private/incognito windows. Grant permissions in:

> **System Settings → Privacy & Security → Accessibility**
> Add your Terminal app (or the app you launch the script from).

> **System Settings → Privacy & Security → Automation**
> Allow your Terminal app to control Safari, Google Chrome, etc.

### 3. OpenAI API key

1. Sign up at [platform.openai.com](https://platform.openai.com)
2. Create an API key at **API Keys → Create new secret key**
3. Ensure your account has billing enabled (gpt-4o-mini is very cheap — ~$0.001 per digest)

### 4. MailerSend account & API key

1. Sign up at [mailersend.com](https://www.mailersend.com)
2. Add and verify a **sender domain** (e.g. `tracker.yourdomain.com`)
3. Go to **API Tokens → Generate new token** with *Full access* or at minimum *Email send* permission
4. Note the verified sender email address you will use as `MAILERSEND_FROM`

### 5. Configure `.env`

```bash
cp .env.template .env
```

Open `.env` and fill in your values:

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Your OpenAI secret key |
| `OPENAI_MODEL` | Model to use (default: `gpt-4o-mini`) |
| `MAILERSEND_API_KEY` | Your MailerSend API token |
| `MAILERSEND_FROM` | Verified sender email address |
| `MAILERSEND_TO` | Recipient email(s) — comma-separated for multiple (e.g. user and accountability partner) |
| `SUMMARY_SCHEDULE` | `hourly` \| `daily` \| `weekly` \| `monthly` |
| `SUMMARY_SCHEDULE_HOUR` | Hour to send (0–23, default `21`) |
| `SUMMARY_SCHEDULE_MINUTE` | Minute to send (0–59, default `0`) |
| `SUMMARY_SCHEDULE_WEEKDAY` | For weekly: `mon`–`sun` (default `mon`) |
| `SUMMARY_SCHEDULE_DAY` | For monthly: day of month 1–28 (default `1`) |

> **Accountability partner tip:** Add both your email and your partner's email to `MAILERSEND_TO`, separated by a comma. Both will receive every digest.

**Schedule examples:**

```bash
# Every day at 9 PM
SUMMARY_SCHEDULE=daily
SUMMARY_SCHEDULE_HOUR=21

# Every hour at :30
SUMMARY_SCHEDULE=hourly
SUMMARY_SCHEDULE_MINUTE=30

# Every Monday at 8 AM
SUMMARY_SCHEDULE=weekly
SUMMARY_SCHEDULE_WEEKDAY=mon
SUMMARY_SCHEDULE_HOUR=8

# 1st of each month at 9 AM
SUMMARY_SCHEDULE=monthly
SUMMARY_SCHEDULE_DAY=1
SUMMARY_SCHEDULE_HOUR=9
```

---

## 🚀 Installation

```bash
bash install.sh
```

This will:
1. Check prerequisites (macOS, Python version, pip, required files, internet)
2. Validate your `.env`
3. Install Python dependencies (`pip install -r requirements.txt`)
4. Copy and configure both launchd services into `~/Library/LaunchAgents/`
5. Load and start both services immediately
6. Send a confirmation email with your configuration summary

---

## 🛑 Uninstall

```bash
bash uninstall.sh
```

You will be prompted whether to also delete log files and your `.env`.

---

## 📄 Logs

| File | Contents |
|---|---|
| `tracker_daemon.log` | Tracker start/stop and polling events |
| `tracker_stderr.log` | Tracker errors (from launchd) |
| `summarizer_daemon.log` | Schedule triggers, OpenAI calls, email status, watchdog events |
| `summarizer_stderr.log` | Summariser errors (from launchd) |
| `detailed_activity_log.txt` | Raw browsing log (timestamped URLs) |
| `detailed_activity_log.txt.sha256` | Tamper-detection checksum (auto-generated) |

---

## 🔐 Privacy & Security

- **What is stored locally:** Full timestamped URLs in `detailed_activity_log.txt`. This file stays on the machine.
- **What is sent to OpenAI:** Domain names only (e.g. `youtube.com`) — never full URLs, paths, or query strings.
- **What is sent via email:** The AI-generated digest summary, which includes domain names and categories.
- **Tamper detection:** A SHA-256 checksum of the log is updated on every write. If the log is edited between writes, the summariser will detect it and alert before sending the digest.
- **Watchdog:** An alert email is sent immediately if the tracking service is stopped.

> Keep your `.env` and log files secure. Do not commit them to version control — both are covered by `.gitignore`.

---

## 🧰 Tech Stack

| Component | Library / Service |
|---|---|
| Browser polling | AppleScript via `subprocess` |
| Scheduling | [APScheduler](https://apscheduler.readthedocs.io/) |
| AI summarisation | [OpenAI Python SDK](https://github.com/openai/openai-python) |
| Email delivery | [MailerSend REST API](https://developers.mailersend.com/) |
| macOS daemon | launchd (`~/Library/LaunchAgents/`) |
