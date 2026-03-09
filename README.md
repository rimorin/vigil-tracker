# Vigil

> **⚠️ macOS only** — this tool uses AppleScript, launchd, and macOS-specific APIs. It does not run on Windows or Linux.

A macOS background service that tracks your daily web browsing across all major browsers, uses **OpenAI** to write a plain-English summary of what you visited, and emails it to you (and optionally a trusted friend) on a schedule you choose — hourly, daily, weekly, or monthly. It also sends an **instant email alert** the moment an adult or harmful site is opened, so your accountability partner is notified right away — not hours later.

---

## 🎯 Purpose & Why This Exists

Most people don't realise how much time they spend on harmful or unproductive websites until they see it written down. Vigil makes your browsing visible — to yourself, or to someone you trust.

### Personal productivity
Get a daily summary of where your time actually went online. Real sites, real categories, real times — no guessing. Use it to spot bad habits and gradually improve them.

### A consent-based accountability system

> **This tool only works with full consent.** It must be installed by the person being monitored — no one else. Installing it on someone's device without them knowing is a serious breach of trust and is likely illegal. Vigil is a tool for honest, mutual support — not spying.

If you're trying to cut back on pornography, gambling, social media, or any other harmful browsing habit, the hardest part isn't knowing what to stop — it's staying honest when no one is watching. That's where accountability helps.

#### Why accountability works

- **You can't hide from what's written down.** It's easy to brush off a bad habit in your head. It's harder when you can see exactly what sites you visited and for how long — and when someone you respect will see it too.
- **A trusted person keeps you honest.** This isn't about punishment. It's about having someone in your corner who you don't want to let down. That feeling alone can stop a bad moment from turning into a bad habit.
- **Instant alerts stop things early.** Most tools only show you what happened yesterday. Vigil emails your partner the moment a harmful site is opened — which means a slip can be caught and talked about right away, before it becomes a pattern.
- **Honesty beats shame.** When you've agreed to share your browsing openly, a slip becomes something you talk about — not something you hide. That's a much healthier way to make progress.
- **You're in control.** You install it yourself, you configure it yourself, and you can remove it any time. Accountability that you choose works far better than accountability forced on you.

#### How it works

You install Vigil on your own computer. Your browsing summary gets emailed to you and your accountability partner on a regular schedule. If you visit an adult or harmful site, an alert email goes out straight away — your partner doesn't have to wait for the next digest. You then talk about it together, honestly. No surveillance, no judgment — just two people working toward the same goal.

---

## ✨ Features

- 🌐 **Multi-browser support** — full URL capture from Safari, Chrome, Edge, Brave, and Arc (including private/incognito windows); window-title fallback for Firefox, Opera, and Tor Browser. See the [Supported Browsers](#-supported-browsers) table for details.
- 🤖 **AI-powered digest** — OpenAI (`gpt-4o-mini` by default) categorises your activity, surfaces top domains, highlights notable browsing sessions, and **explicitly flags** any adult, gambling, or self-harm content
- 📧 **Email delivery** — sends a clean HTML email via SMTP (no third-party service required) with Overview, Top Domains, Categories, Timeline Highlights, and Concern Flags sections
- 🚨 **Live alert emails** — `alerter.py` detects adult/harmful sites in real time (offline, using a domain blocklist + keyword matching) and fires an immediate SMTP alert email the moment a match is found — no waiting for the next digest. A per-domain cooldown prevents alert spam.
- ⏰ **Flexible schedule** — `hourly`, `daily`, `weekly`, or `monthly` digest controlled entirely by environment variables (powered by APScheduler)
- 🚀 **Runs as a macOS daemon** — both the tracker and summariser are managed by launchd: auto-start on login, auto-restart on crash
- 🛡️ **Tamper detection** — a SHA-256 checksum sidecar file is updated on every log write; the summariser alerts if the log has been modified between writes
- 👁️ **Watchdog** — the summariser checks every 5 minutes that the tracker service is still running, and sends an immediate alert if it has been stopped
- 🔐 **Privacy-preserving AI** — only domain names (not full URLs or paths) are sent to OpenAI; complete URLs remain on the local machine only
- **Secrets via env vars** — no credentials ever hardcoded; all keys live in `.env` and are loaded at runtime by the Python services. API keys are **not** embedded in the launchd plist files in `~/Library/LaunchAgents/`.

---

## 🌐 Supported Browsers

| Browser | URL capture | Private / Incognito |
|---|---|---|
| Safari | ✅ Full URL (AppleScript `current tab`) | ✅ Yes |
| Google Chrome | ✅ Full URL (AppleScript `active tab`) | ✅ Yes |
| Microsoft Edge | ✅ Full URL (AppleScript `active tab`) | ✅ Yes |
| Brave | ✅ Full URL (AppleScript `active tab`) | ✅ Yes |
| Arc | ✅ Full URL (AppleScript `active tab`) | ✅ Yes |
| Firefox | ⚠️ Window title only (no AppleScript URL access) | ⚠️ Title only |
| Opera | ⚠️ Window title only (no AppleScript URL access) | ⚠️ Title only |
| Tor Browser | ⚠️ Window title only (no AppleScript URL access) | ⚠️ Title only |

> **Full URL capture** requires Automation permission for that browser in **System Settings → Privacy & Security → Automation**. Browsers with window-title fallback are still tracked, but domain-level detail and AI categorisation will be less accurate.

---

## 📁 Project Structure

```
personal_tracker/
├── tracker.py                          # Browser polling daemon (AppleScript + Python)
├── summarizer.py                       # Long-running scheduler daemon (APScheduler + OpenAI)
├── alerter.py                          # Real-time adult/harmful site detection and live email alerts
├── config.py                           # Environment variable loader
├── requirements.txt                    # Python dependencies
├── com.vigil.tracker.plist               # launchd template — tracker service
├── com.vigil.summarizer.plist           # launchd template — summariser service
├── install.sh                          # One-command install script
├── uninstall.sh                        # One-command uninstall script
├── .env.template                       # Configuration template (copy to .env)
├── data/
│   └── adult_domains.txt               # Offline blocklist used by alerter.py
├── detailed_activity_log.txt.sha256    # Tamper-detection checksum (auto-generated)
└── tests/
    ├── conftest.py                     # Shared pytest fixtures and env stubs
    ├── test_tracker.py                 # Tests for tracker.py logic
    └── test_summarizer.py              # Tests for summarizer.py logic
```

---

## ✅ Prerequisites

Complete **all** of the following before running `install.sh`.

### 1. macOS version

| macOS Version | Status | Notes |
|---|---|---|
| 15 Sequoia (2024) | ✅ Fully supported | Uses `launchctl bootstrap/bootout` |
| 14 Sonoma (2023) | ✅ Fully supported | Uses `launchctl bootstrap/bootout` |
| 13 Ventura (2022) | ✅ Fully supported | Uses `launchctl bootstrap/bootout` |
| 12 Monterey (2021) | ✅ Fully supported | |
| 11 Big Sur (2020) | ✅ Fully supported | |
| 10.15 Catalina (2019) | ✅ Minimum supported | |
| 10.14 Mojave or earlier | ❌ Not supported | Automation privacy permissions not enforced; AppleScript behaviour differs |

The `install.sh` script automatically uses the correct launchctl commands for your macOS version (`bootstrap`/`bootout` on macOS 13+, `load`/`unload` on older versions). No manual commands needed.

### 2. Python 3.8+

```bash
python3 --version   # must be 3.8 or higher
```

Install via [Homebrew](https://brew.sh) if needed:

```bash
brew install python
```

### 3. Accessibility & Automation permissions

The tracker uses AppleScript to read browser tabs — including private/incognito windows. These permissions are required on **macOS 10.15 Catalina and later**.

On **macOS 13 Ventura and later** (System Settings):
> **System Settings → Privacy & Security → Accessibility** — add Terminal
> **System Settings → Privacy & Security → Automation** — allow Terminal to control Safari, Chrome, etc.

On **macOS 10.15–12 Monterey** (System Preferences):
> **System Preferences → Security & Privacy → Privacy → Accessibility** — add Terminal
> **System Preferences → Security & Privacy → Privacy → Automation** — allow Terminal to control browsers

### 4. OpenAI API key

1. Sign up at [platform.openai.com](https://platform.openai.com)
2. Create an API key at **API Keys → Create new secret key**
3. Ensure your account has billing enabled (gpt-4o-mini is very cheap — ~$0.001 per digest)

### 5. SMTP email credentials

Vigil sends emails using standard SMTP — no third-party account or API key required beyond your existing email provider.

| Provider | SMTP host | Port |
|---|---|---|
| Gmail | `smtp.gmail.com` | 587 |
| Outlook / Microsoft 365 | `smtp.office365.com` | 587 |
| Fastmail | `smtp.fastmail.com` | 587 |
| Apple iCloud | `smtp.mail.me.com` | 587 |
| Any provider | check your provider's SMTP settings | 587 or 465 |

> **Gmail users:** standard passwords won't work — you must create an **App Password** at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). Enable 2-Step Verification first if you haven't already.

### 6. Configure `.env`

The `install.sh` wizard will prompt for all required values and write `.env` for you. If you prefer to configure it manually:

```bash
cp .env.template .env
```

Open `.env` and fill in your values:

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Your OpenAI secret key |
| `OPENAI_MODEL` | Model to use (default: `gpt-4o-mini`) |
| `SMTP_HOST` | SMTP server hostname (e.g. `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (default: `587` for STARTTLS, or `465` for SSL) |
| `SMTP_USER` | SMTP login username (usually your full email address) |
| `SMTP_PASS` | SMTP password or app password |
| `SMTP_FROM` | Sender address (optional — defaults to `SMTP_USER`) |
| `SMTP_TO` | Recipient email(s) — comma-separated for multiple (e.g. user and accountability partner) |
| `SUMMARY_SCHEDULE` | `hourly` \| `daily` \| `weekly` \| `monthly` |
| `SUMMARY_SCHEDULE_HOUR` | Hour to send (0–23, default `21`) |
| `SUMMARY_SCHEDULE_MINUTE` | Minute to send (0–59, default `0`) |
| `SUMMARY_SCHEDULE_WEEKDAY` | For weekly: `mon`–`sun` (default `mon`) |
| `SUMMARY_SCHEDULE_DAY` | For monthly: day of month 1–28 (default `1`) |
| `ADULT_ALERT_ENABLED` | Enable real-time adult content detection (`true` / `false`, default `true`) |
| `ADULT_ALERT_EMAIL` | Send a live alert email when an adult site is detected (`true` / `false`, default `true`) |
| `ADULT_ALERT_COOLDOWN_MINUTES` | Minutes before the same domain can trigger another alert — prevents spam (default `30`) |

> **Accountability partner tip:** Add both your email and your partner's email to `SMTP_TO`, separated by a comma. Both will receive every scheduled digest *and* every live alert email the moment an adult or harmful site is detected.

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

The installer will:
1. Check prerequisites (macOS, Python 3.8+, pip, required files)
2. **Prompt for any missing API keys** — no manual `.env` editing needed
3. **Validate your credentials** against the OpenAI API and your SMTP server before proceeding
4. **Remind you to grant macOS permissions** and offer to open System Settings directly
5. Install Python dependencies (`pip install -r requirements.txt`)
6. Install and start both launchd services (using the correct commands for your macOS version)
7. Send a confirmation email with your configuration summary

> **Already installed?** Re-run `bash install.sh` any time to update credentials or re-register services after moving the project directory.

### Check service health

```bash
bash install.sh --status
```

Shows whether each service is running, and tails the last few lines of each log file.

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

## 🧪 Testing

The test suite covers all pure-logic functions in `tracker.py` and `summarizer.py`. No real log files, SMTP servers, or OpenAI API calls are made — all file I/O is redirected to temporary directories via pytest fixtures.

### Run the tests

```bash
.venv/bin/pytest tests/ -v
```

### What is tested

| File | Test class | What it covers |
|---|---|---|
| `tracker.py` | `TestUpdateIntegrityHash` | SHA-256 sidecar written correctly; no-op when log is absent |
| `tracker.py` | `TestLogDurationEntry` | Entry format, timestamp pattern, hash updated on write |
| `tracker.py` | `TestFinalizeSession` | Basic duration, idle gap excluded, open idle counted, short sessions discarded |
| `tracker.py` | `TestGetLastLogTime` | Parses last-line timestamp; handles empty file, missing file, unparseable line |
| `tracker.py` | `TestCheckForShutdownEvent` | Logs shutdown event when boot time is after last activity; no-op otherwise |
| `tracker.py` | `TestGetActiveTabAppleScript` | Script contains Safari block and System Events block |
| `summarizer.py` | `TestCleanupOldEntries` | Removes old entries, keeps recent ones, keeps undated lines (SYSTEM EVENT), no-op for zero-day retention, atomic write, hash refresh, no temp file left behind |
| `summarizer.py` | `TestStripToDomain` | Full URLs stripped to domain; timestamps and durations preserved |
| `summarizer.py` | `TestParseDurationEntries` | Accumulates time per domain; ignores undated lines |
| `summarizer.py` | `TestFormatDuration` | Seconds → `30s`, `1m`, `1h`, `1h 1m` (parametrised) |
| `summarizer.py` | `TestBuildTimePerDomainHtml` | Top-5 cap, empty input, total time displayed |
| `summarizer.py` | `TestHtmlToText` | Tags stripped, excess whitespace collapsed |
| `summarizer.py` | `TestSentinelFile` | Today/yesterday/missing sentinel; `_mark_sent_today` round-trip |
| `summarizer.py` | `TestVerifyLogIntegrity` | Valid hash passes, tampered log fails, missing files treated as valid |

### Adding tests

`tests/conftest.py` stubs all required environment variables so `config.py` can be imported without a real `.env`. Use the `log_env` and `sentinel_env` fixtures (defined in `test_summarizer.py`) to redirect file paths to `tmp_path` and keep tests hermetic.

---



---

## 🔧 Tech Stack

| Component | Library / Service | macOS requirement |
|---|---|---|
| Browser polling | AppleScript via `subprocess` | 10.15 Catalina+ (Automation permission) |
| Scheduling | [APScheduler](https://apscheduler.readthedocs.io/) | — |
| AI summarisation | [OpenAI Python SDK](https://github.com/openai/openai-python) | — |
| Email delivery | SMTP via Python `smtplib` (stdlib — no extra package) | — |
| macOS daemon | launchd (`~/Library/LaunchAgents/`) | 10.15 Catalina+ (`bootstrap`/`bootout` on 13+, `load`/`unload` on older) |
