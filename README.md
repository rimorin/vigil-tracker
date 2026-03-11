# Vigil

> **🧪 Experimental** — early-stage project. Things may break. Please report any issues you find.

Vigil runs quietly in the background on your computer. It watches which websites you visit — including private/incognito windows — and emails a digest to you and a trusted accountability partner on a schedule you choose. If a pornographic site is opened, an alert goes out within minutes.

---

## Table of Contents

- [Why This Exists](#-why-this-exists)
- [Features](#-features)
- [Supported Browsers](#-supported-browsers)
- [Project Structure](#-project-structure)
- [Partner PIN Protection](#-partner-pin-protection)
- [Updating the Domain Blocklist](#-updating-the-domain-blocklist)
- [How Alert Detection Works](#-how-alert-detection-works)
- [Before You Install](#-before-you-install)
- [Installation](#-installation)
- [Uninstall](#-uninstall)
- [Log Files](#-log-files)
- [Settings Reference](#️-settings-reference)
- [Privacy](#-privacy)
- [Tests](#-tests)
- [Tech Stack](#-tech-stack)
- [References](#-references)

---

## 🎯 Why This Exists

Vigil is for people who want to break a pornography habit by bringing their browsing into the open — so they're not fighting it alone.

Pornography is more accessible than ever — available on any device, at any time, in complete privacy. The shame that once acted as a natural barrier has been replaced by invisibility — and that invisibility is what makes it dangerous.

> *"Countless viewers of pornography damage their marriage, personal dignity, and conscience."*
> — [Pornography — A Dangerous Trap](https://www.jw.org/en/library/magazines/watchtower-study-june-2019/pornography-a-dangerous-trap/)

The habit survives because it hides. Images leave lasting imprints, resurface without warning, and a single unguarded moment can undo months of progress. Most people believe they can handle it alone — most find, after years of trying, that they cannot.

> *"You think you can beat it by yourself. But that is not true. You can beat it only with help."*
> — Yoshi, quoted in [Pornography — Harmless or Toxic?](https://www.jw.org/en/library/magazines/wp20130801/is-pornography-harmful/)

Removing the hiding place — including incognito mode — is what breaks the cycle. Vigil does that.

> **Vigil must be installed by the person being monitored — no one else.** This is a tool for mutual support, not surveillance. You choose to install it. You choose your partner.

---

## ✨ Features

- 🌐 **All major browsers** — full URLs in Safari, Chrome, Edge, Brave, and Arc on macOS; Chrome, Edge, Brave, Firefox and more on Windows. See [Supported Browsers](#-supported-browsers).
- 🚨 **Periodic alerts** — the tracker tags each adult-site visit in the activity log with `[FLAGGED_CONTENT]`; a background scan runs every few minutes (configurable) and sends one consolidated alert email per cycle if any flagged visits are found. Works reliably on both macOS and Windows.
- 🤖 **AI digest** *(optional)* — with an OpenAI key, summaries include categories, timeline highlights, and flagged content analysis. Without one, a plain visit list (domains, time spent, full log) is sent instead — no external calls needed.
- 📧 **Email via your own account** — standard SMTP. Gmail, Outlook, iCloud, Fastmail — any provider works.
- ⏰ **Flexible schedule** — hourly, daily, weekly, monthly, or custom interval.
- 🚀 **Always running** — starts on login, restarts on crash via macOS launchd or Windows Task Scheduler.
- 🛡️ **Tamper detection** — if the log file is edited, an alert is sent before the next digest.
- 👁️ **Watchdog** — if the tracker stops unexpectedly, an alert goes out immediately.
- 🔑 **Partner PIN protection** — your accountability partner sets a PIN at install time — and keeps it to themselves. Without it, Vigil cannot be uninstalled. The PIN is hashed and stored securely in the OS keychain — not in any file you can edit. Three failed attempts triggers an immediate alert to your partner. See [Partner PIN Protection](#-partner-pin-protection).
- 🔐 **Private** — only domain names (e.g. `youtube.com`) are ever sent to OpenAI. Full URLs stay on your machine.

---

## 🌐 Supported Browsers

### macOS

| Browser | Full URL | Private / Incognito |
|---|---|---|
| Safari | ✅ | ✅ |
| Google Chrome | ✅ | ✅ |
| Microsoft Edge | ✅ | ✅ |
| Brave | ✅ | ✅ |
| Arc | ✅ | ✅ |
| Firefox | ⚠️ Page title only | ⚠️ |
| Opera | ⚠️ Page title only | ⚠️ |
| Tor Browser | ⚠️ Page title only | ⚠️ |

### Windows

| Browser | Full URL | Private / Incognito | Notes |
|---|---|---|---|
| Microsoft Edge | ✅ | ✅ | Best support — stable AutomationId |
| Google Chrome | ✅ | ✅ | Chrome 138+ (mid-2025); English locale only* |
| Brave | ✅ | ✅ | Chromium-based; same as Chrome |
| Vivaldi | ✅ | ✅ | Chromium-based; same as Chrome |
| Firefox | ✅ | ✅ | Via accessibility API |
| Opera | ⚠️ Page title only | ⚠️ | Non-standard address bar |

> ⚠️ browsers are still tracked, but with less detail. For best results, use a ✅ browser.
>
> *Chrome on Windows uses a locale-sensitive label to find the address bar. On non-English Windows, Edge is the recommended choice for full URL capture.

---

## 📁 Project Structure

```
vigil-tracker/
├── tracker.py                  # Watches browser tabs every few seconds
├── summarizer.py               # Sends scheduled digest emails
├── alerter.py                  # Sends instant alerts for porn sites
├── pin_auth.py                 # Partner PIN hashing and OS keychain storage
├── config.py                   # Reads settings from .env
├── platform_common.py          # Shared OS path helpers
├── requirements.txt            # Python dependencies
├── platforms/
│   ├── windows/
│   │   ├── tracker_windows.py  # Windows idle + UIA URL detection
│   │   ├── install.bat         # Launcher (bypasses execution policy)
│   │   ├── install.ps1         # Windows one-command setup (PowerShell)
│   │   ├── uninstall.bat       # Uninstaller launcher
│   │   ├── uninstall.ps1       # Windows one-command removal (PowerShell)
│   │   ├── vigil-tracker.xml   # Task Scheduler template (tracker)
│   │   └── vigil-summarizer.xml # Task Scheduler template (summarizer)
│   └── macos/
│       ├── tracker_macos.py    # macOS idle + AppleScript URL detection
│       ├── install.sh          # macOS one-command setup
│       ├── uninstall.sh        # macOS one-command removal
│       ├── com.vigil.tracker.plist    # launchd config (tracker)
│       └── com.vigil.summarizer.plist # launchd config (summarizer)
├── .env.template               # Settings template — copy to .env and fill in
├── data/
│   └── domains.txt             # Offline blocklist for instant alerts
└── tests/
    ├── conftest.py
    ├── test_tracker.py
    ├── test_summarizer.py
    ├── test_alerter.py
    ├── test_pin_auth.py
    └── test_windows.py
```

---

## 🔒 Partner PIN Protection

People remove accountability tools in moments of weakness. The partner PIN prevents that.

> **You should not know your own PIN.** Your partner sets it at install time and keeps it. Without it, Vigil cannot be uninstalled — turning an impulsive decision into a conversation.

- PIN is hashed with PBKDF2-HMAC-SHA256 — never stored in plain text
- Stored in the OS keychain (macOS Keychain / Windows Credential Locker)
- Three wrong attempts → immediate alert email to your partner

**At install:** hand the keyboard to your partner, they enter and confirm the PIN, you look away.

```bash
python pin_auth.py hash    # set PIN — your partner runs this
python pin_auth.py verify  # verify PIN
python pin_auth.py delete  # remove PIN — partner only
python pin_auth.py status  # check if PIN is set
```

---

## 🗂 Updating the Domain Blocklist

Vigil detects pornographic sites by matching visited domains against `data/domains.txt` — a plain-text file with one domain per line (comments start with `#`).

**To add domains manually**, open `data/domains.txt` and append entries:

```
example-adult-site.com
another-site.net
```

**To replace the list with a fresh community blocklist**, download any plain-text domain blocklist and drop it in as `data/domains.txt`. A commonly used source:

```bash
# Example — Steven Black's unified hosts list (porn category)
curl -o data/domains.txt \
  "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn-only/hosts"

# Strip the hosts file format down to bare domains
grep -v '^#' data/domains.txt | grep -v '^$' | awk '{print $2}' | grep -v '^0\.0\.0\.0$' > data/domains_clean.txt
mv data/domains_clean.txt data/domains.txt
```

> Vigil reads the blocklist once at startup. After editing `domains.txt`, restart the tracker service for changes to take effect.
>
> **macOS:**
> ```bash
> launchctl unload ~/Library/LaunchAgents/com.vigil.tracker.plist
> launchctl load  ~/Library/LaunchAgents/com.vigil.tracker.plist
> ```
>
> **Windows** (in an elevated Command Prompt):
> ```bat
> schtasks /End /TN "Vigil Tracker"
> schtasks /Run /TN "Vigil Tracker"
> ```

---

## 🚨 How Alert Detection Works

Vigil uses a **log-tagging + periodic scan** approach instead of trying to fire an alert the instant a site is opened. This makes the system reliable on both macOS and Windows.

### Step 1 — Tag visits in the activity log

The tracker polls the active browser tab every few seconds. When it detects a domain that matches the blocklist, it sets an `is_adult` flag on the current session.

When the session ends (tab closed, navigated away, or idle), the tracker writes a line to `detailed_activity_log.txt` in the usual format, with `[FLAGGED_CONTENT]` appended:

```
[2025-11-14 22:03:41] example-adult-site.com [duration: 47s] [FLAGGED_CONTENT]
```

Normal, non-adult visits are logged identically but without the tag:

```
[2025-11-14 22:04:30] youtube.com [duration: 120s]
```

### Step 2 — Periodic scan for flagged entries

A background daemon thread wakes up every `ALERT_SCAN_INTERVAL_MINUTES` (default: 5 minutes). Each time it runs it:

1. Reads the **cursor** — a timestamp stored in `alerter_cursor.txt` marking how far through the log was read last time
2. Scans only the **new lines** added since that cursor
3. Collects every line containing `[FLAGGED_CONTENT]`
4. Advances the cursor to `now` so lines aren't re-read next cycle
5. If any flagged visits were found — sends **one consolidated alert email** listing every visit from that scan window

```
Subject: ⚠️ Vigil Alert — Flagged content detected

The following adult/pornographic sites were visited:

  22:03  example-adult-site.com  (47s)
  22:11  another-flagged-site.com  (12s)
```

### Why this approach

| Property | Old (real-time) | New (log-scan) |
|---|---|---|
| macOS reliability | ✅ | ✅ |
| Windows reliability | ❌ Unstable | ✅ |
| Alert latency | Immediate | ≤ scan interval (default 5 min) |
| Duplicate emails | Possible | Impossible — one email per scan |
| SMTP failure impact | Stalls main loop | Isolated to background thread |
| Email on slow SMTP | Could block tracking | Never blocks tracking |

### Sequence diagram

```
Tracker loop (every ~5s)                   Alert daemon (every 5 min)
─────────────────────────                  ──────────────────────────
Poll active tab
  └─ adult domain? → is_adult = true

Session ends
  └─ write log line
       └─ is_adult? → append [FLAGGED_CONTENT]
                                           Wake up
                                             └─ read cursor
                                             └─ scan new log lines
                                             └─ collect [FLAGGED_CONTENT] lines
                                             └─ advance cursor
                                             └─ any found? → send email
```

### Configuring the scan interval

Set `ALERT_SCAN_INTERVAL_MINUTES` in your `.env` file. Shorter = faster alerts, but more frequent SMTP calls if visits are frequent.

```bash
ALERT_SCAN_INTERVAL_MINUTES=5   # default — alert within 5 minutes of a visit
ALERT_SCAN_INTERVAL_MINUTES=1   # near-real-time
ALERT_SCAN_INTERVAL_MINUTES=10  # less frequent; fine for most use cases
```

---

## ✅ Before You Install

### macOS requirements

#### 1. macOS 10.15 Catalina or newer

| Version | Supported |
|---|---|
| 15 Sequoia – 10.15 Catalina | ✅ |
| 10.14 Mojave or older | ❌ |

#### 2. Python 3.8 or newer

```bash
python --version
```

No Python? Install via [Homebrew](https://brew.sh):

```bash
brew install python
```

#### 3. Browser access permissions

Vigil uses macOS Automation to read browser tabs (including private windows). You need to grant Terminal access in your Mac's privacy settings.

- **macOS 13+:** System Settings → Privacy & Security → Accessibility → add Terminal, then Automation → allow Terminal to control your browsers.
- **macOS 10.15–12:** System Preferences → Security & Privacy → Privacy → Accessibility → add Terminal, then Automation → allow Terminal to control your browsers.

> `install.sh` will offer to open the correct settings screen for you.

---

### Windows requirements

#### 1. Windows 10 (build 17763) or newer

```powershell
[System.Environment]::OSVersion
```

#### 2. Python 3.8 or newer

Download from [python.org](https://python.org) and tick **"Add Python to PATH"** during setup.

```powershell
python --version
```

#### 3. PowerShell execution policy

Windows blocks `.ps1` scripts by default. The simplest fix is to use the
provided batch wrapper — it bypasses the policy **only for this script**
without changing any system setting:

```bat
install.bat
```

If you prefer to run the PowerShell script directly, allow user-level scripts once first:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\install.ps1
```

#### 4. Browser address bar access

No special flags or configuration needed. Vigil reads browser URLs via the Windows Accessibility API (UI Automation):

- **Chrome 138+** — native UIA enabled by default (released mid-2025)
- **Edge** — always supported, most reliable
- **Firefox** — always supported via accessibility API

---

### OpenAI API key *(optional)*

Skip this if you don't have one — Vigil will still send a plain visit list digest generated entirely on your machine.

For the full AI summary (categories, timeline highlights, flagged content analysis):
1. Sign up at [platform.openai.com](https://platform.openai.com)
2. Create an API key under **API Keys**
3. Enable billing (~$0.001 per digest)

### Email account (SMTP)

Vigil sends email through your own existing email account using SMTP — no third-party service needed. You provide your email address, an app password, and your provider's server address.

| Provider | `SMTP_HOST` | `SMTP_PORT` |
|---|---|---|
| Gmail | `smtp.gmail.com` | `587` |
| Outlook / Microsoft 365 | `smtp.office365.com` | `587` |
| Fastmail | `smtp.fastmail.com` | `587` |
| Apple iCloud | `smtp.mail.me.com` | `587` |

**App passwords (required for most providers):**

- **Gmail** — [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). Requires 2-Step Verification.
- **iCloud** — [appleid.apple.com](https://appleid.apple.com) → Sign-In and Security → App-Specific Passwords.
- **Fastmail** — [fastmail.com/settings/security](https://www.fastmail.com/settings/security/).
- **Outlook** — normal password usually works; generate an app password if your org enforces MFA.

> **Tip:** Put both your address and your partner's in `SMTP_TO` (comma-separated). Both receive every digest and every alert. You can use a spare account as the sender.

---

## ⚙️ Settings Reference

The installer will prompt for everything interactively. To configure manually:

```bash
cp .env.template .env
```

| Setting | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | *(empty)* | Optional. Leave blank for plain visit list digest. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Only used when API key is set. |
| `SMTP_HOST` | — | Your provider's SMTP address |
| `SMTP_PORT` | `587` | Use `465` for SSL-only providers |
| `SMTP_USER` | — | Sender email address |
| `SMTP_PASS` | — | Password or app password |
| `SMTP_FROM` | `SMTP_USER` | Display sender address |
| `SMTP_TO` | — | Recipient(s), comma-separated |
| `SUMMARY_SCHEDULE` | `daily` | `hourly` / `daily` / `weekly` / `monthly` / `interval` |
| `SUMMARY_SCHEDULE_HOUR` | `21` | Hour to send (0–23) |
| `SUMMARY_SCHEDULE_MINUTE` | `0` | Minute to send (0–59) |
| `SUMMARY_SCHEDULE_WEEKDAY` | `mon` | `mon`–`sun` (weekly only) |
| `SUMMARY_SCHEDULE_DAY` | `1` | Day 1–28 (monthly only) |
| `SUMMARY_SCHEDULE_INTERVAL_MINUTES` | `60` | Minutes between digests (interval only) |
| `LOG_RETENTION_DAYS` | `30` | Activity log entries older than this many days are pruned |
| `ALERT_ENABLED` | `true` | Enable/disable alerts |
| `ALERT_EMAIL` | `true` | Send alert via email |
| `ALERT_SCAN_INTERVAL_MINUTES` | `5` | How often (in minutes) to scan the log for flagged visits |

**Schedule examples:**

```bash
# Every day at 9 PM
SUMMARY_SCHEDULE=daily
SUMMARY_SCHEDULE_HOUR=21

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

### macOS

```bash
bash platforms/macos/install.sh
```

The installer will:
1. Verify macOS version, Python, and required files
2. Prompt for any missing credentials
3. Validate your SMTP connection (and OpenAI key if provided)
4. Open macOS privacy settings for browser access
5. Install Python packages
6. Invite your partner to set a PIN (stored securely in macOS Keychain — only they should know it)
7. Start both background services (auto-restart on crash, auto-start on login)
8. Send a confirmation email

```bash
bash platforms/macos/install.sh --status   # check if services are running
bash platforms/macos/install.sh            # re-run any time to update settings or restart services
```

### Windows

Navigate into `platforms\windows\`, then double-click **`install.bat`**, or open a Command Prompt / PowerShell in the project root and run:

```bat
platforms\windows\install.bat
```

The `.bat` wrapper bypasses the PowerShell execution policy for this script only — no system changes needed.

If you prefer to run the PowerShell script directly, see the [execution policy note](#3-powershell-execution-policy) above, then:

```powershell
.\platforms\windows\install.ps1
```

The installer will:
1. Verify Windows 10+ and Python 3.8+
2. Walk you through `.env` configuration interactively
3. Validate your SMTP connection
4. Install Python packages via pip
5. Invite your partner to set a PIN (stored securely in Windows Credential Locker — only they should know it)
6. Register both services in Task Scheduler (auto-restart on crash, auto-start at logon)

```bat
platforms\windows\install.bat -Status      # check if services are running
platforms\windows\install.bat -Reinstall   # re-register tasks after moving the folder or upgrading Python
```

---

### After installation

**Vigil runs silently in the background:**
- **Every ~5 seconds** — logs the active browser tab and time spent; flags adult-site visits with `[FLAGGED_CONTENT]` in the log
- **Every N minutes** — scans the log for new `[FLAGGED_CONTENT]` entries; sends one consolidated alert email per cycle if any are found (interval set by `ALERT_SCAN_INTERVAL_MINUTES`)
- **On schedule** — sends your digest (AI summary or plain visit list)
- **Every 5 minutes** — checks the tracker is running; alerts if it stops

---

## 🛑 Uninstall

> **Partner PIN required** — if a partner PIN was set during installation, you will need to enter it before the uninstaller proceeds. Three failed attempts will trigger an alert email to your accountability partner.

### macOS

```bash
bash platforms/macos/uninstall.sh
```

### Windows

```bat
platforms\windows\uninstall.bat
```

Or directly:

```powershell
.\platforms\windows\uninstall.ps1
```

You'll be asked whether to also delete your log files and settings.

---

## 📄 Log Files

| Platform | Location |
|---|---|
| macOS | `~/Library/Logs/Vigil/` and `~/Library/Application Support/Vigil/` |
| Windows | `%LOCALAPPDATA%\Vigil\Logs\` and `%APPDATA%\Vigil\` |

| File | Contents |
|---|---|
| `tracker_daemon.log` | Tracker start/stop events and polled URLs |
| `tracker_stderr.log` | Tracker errors |
| `summarizer_daemon.log` | Digest sends, API calls, watchdog checks |
| `summarizer_stderr.log` | Summariser errors |
| `alerter.log` | Adult-site detections and alert email results (check here if alerts aren't arriving) |
| `detailed_activity_log.txt` | Full browsing log with timestamps |
| `detailed_activity_log.txt.sha256` | Tamper-detection hash |

---

## 🔐 Privacy

| Data | Where it goes |
|---|---|
| Full URLs + timestamps | Stored locally only — never leaves your machine |
| Domain names only | Sent to OpenAI for AI summaries — only if `OPENAI_API_KEY` is set |
| Digest content | Sent by email to your chosen recipients |

> Keep `.env` private — it contains your credentials. It's already in `.gitignore`.

---

## 🧪 Tests

**macOS / Linux:**
```bash
.venv/bin/pytest tests/ -v
```

**Windows:**
```bat
.venv\Scripts\pytest tests/ -v
```

No real browsing data, email accounts, or OpenAI calls are used — everything runs against temporary files.

| File | What is tested |
|---|---|
| `test_tracker.py` | Log writing, timestamps, hash updates, session detection, `[FLAGGED_CONTENT]` tagging, shutdown events |
| `test_summarizer.py` | Log cleanup, domain parsing, time totals, email formatting, tamper detection |
| `test_alerter.py` | Adult-domain detection, `[FLAGGED_CONTENT]` log tagging, cursor-based log scanning, consolidated alert email |
| `test_pin_auth.py` | PIN hashing, PBKDF2 verification, keychain storage/retrieval/deletion, failed-attempt lockout and alert email |
| `test_windows.py` | Windows idle detection, UIA URL reading, active-window label (fully mocked) |

---

## 🔧 Tech Stack

| Component | Tool |
|---|---|
| Browser watching (macOS) | AppleScript via Python `subprocess` |
| Browser watching (Windows) | Windows UI Automation via `uiautomation` + `ctypes` |
| Scheduling | APScheduler |
| AI summaries | OpenAI Python SDK *(optional)* |
| Email | Python `smtplib` |
| PIN storage | `keyring` (macOS Keychain / Windows Credential Locker) |
| Background services (macOS) | launchd |
| Background services (Windows) | Windows Task Scheduler |

---

## 📚 References

- *Pornography — Harmless or Toxic?* — [jw.org](https://www.jw.org/en/library/magazines/wp20130801/is-pornography-harmful/)
- *Pornography — A Dangerous Trap* — [jw.org](https://www.jw.org/en/library/magazines/watchtower-study-june-2019/pornography-a-dangerous-trap/)
