# Vigil

> **🧪 Experimental** — early-stage project. Things may break. Please report any issues you find.

> **⚠️ macOS only** — Windows and Linux support is coming.

Vigil runs quietly in the background on your Mac. It watches which websites you visit — in both normal and private/incognito windows — and emails a digest to you and a trusted friend on a schedule you choose. If a pornographic site is opened, an alert goes out immediately.

---

## 🎯 Why This Exists

Vigil is for people who want to break a pornography habit. It brings your browsing into the open — so you're not fighting it alone.

### Why the name "Vigil"

A vigil means keeping watch through the night so nothing slips by. That's what this tool does — quietly, in the background. The name also reflects the role of your accountability partner: not a warden, but someone who is *with you* in the fight.

### Why willpower alone rarely works

- **The habit hides in secrecy.** Shame keeps it private; privacy keeps it alive. Removing the hiding place — including private/incognito mode — is what breaks the cycle.
- **The brain gets wired for it.** Pornography floods the brain with dopamine and leaves lasting imprints. Even after stopping, urges can return without warning. Ongoing accountability is what keeps those moments from becoming relapses.
- **You can't beat it alone.** As one person put it: *"You think you can beat it by yourself. But that is not true. You can beat it only with help."*

### How Vigil helps

- **No blind spots.** Vigil tracks both normal and incognito browsing. When you know someone will see your activity, the private space where the habit lives disappears.
- **Fast response.** An alert goes to your partner the moment a pornographic site is opened — before a slip turns into a longer session.
- **Visible progress.** The regular digest shows patterns over time: when temptation tends to hit, whether things are improving, and when a slip happened. Seeing that in writing is hard to argue with.

### Built on consent

> **Vigil must be installed by the person being monitored — no one else.** Installing it on someone's device without their knowledge is a serious breach of trust and likely illegal. This is a tool for mutual support, not surveillance.

You choose to install it. You choose your partner. You can remove it any time.

---

## ✨ Features

- 🌐 **All major browsers** — full URLs in Safari, Chrome, Edge, Brave, and Arc, including private/incognito. Falls back to page title for Firefox, Opera, and Tor. See [Supported Browsers](#-supported-browsers).
- 🚨 **Instant alerts** — alert email sent the moment a pornographic site is detected, with a configurable cooldown per domain.
- 🤖 **AI digest** *(optional)* — with an OpenAI key, summaries include categories, timeline highlights, and flagged content analysis. Without one, a plain visit list (domains, time spent, full log) is sent instead — no external calls needed.
- 📧 **Email via your own account** — standard SMTP. Gmail, Outlook, iCloud, Fastmail — any provider works.
- ⏰ **Flexible schedule** — hourly, daily, weekly, or monthly.
- 🚀 **Always running** — starts on login, restarts on crash via macOS launchd.
- 🛡️ **Tamper detection** — if the log file is edited, an alert is sent before the next digest.
- 👁️ **Watchdog** — if the tracker stops unexpectedly, an alert goes out immediately.
- 🔐 **Private** — only domain names (e.g. `youtube.com`) are ever sent to OpenAI. Full URLs stay on your machine.

---

## 🌐 Supported Browsers

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

> ⚠️ browsers are still tracked, but with less detail. For best results, use a ✅ browser.

---

## 📁 Project Structure

```
personal_tracker/
├── tracker.py                  # Watches browser tabs every few seconds
├── summarizer.py               # Sends scheduled digest emails
├── alerter.py                  # Sends instant alerts for porn sites
├── config.py                   # Reads settings from .env
├── requirements.txt            # Python dependencies
├── com.vigil.tracker.plist     # launchd config for tracker
├── com.vigil.summarizer.plist  # launchd config for summarizer
├── install.sh                  # One-command setup
├── uninstall.sh                # One-command removal
├── .env.template               # Settings template — copy to .env and fill in
├── data/
│   └── domains.txt             # Offline blocklist for instant alerts
└── tests/
    ├── conftest.py
    ├── test_tracker.py
    └── test_summarizer.py
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

> Vigil reads the blocklist once at startup. After editing `domains.txt`, restart the tracker service for changes to take effect:
> ```bash
> launchctl unload ~/Library/LaunchAgents/com.vigil.tracker.plist
> launchctl load  ~/Library/LaunchAgents/com.vigil.tracker.plist
> ```

---

## ✅ Before You Install

### 1. macOS 10.15 Catalina or newer

| Version | Supported |
|---|---|
| 15 Sequoia – 10.15 Catalina | ✅ |
| 10.14 Mojave or older | ❌ |

### 2. Python 3.8 or newer

```bash
python3 --version
```

No Python? Install via [Homebrew](https://brew.sh):

```bash
brew install python
```

### 3. Browser access permissions

Vigil uses macOS Automation to read browser tabs (including private windows). You need to grant Terminal access in your Mac's privacy settings.

- **macOS 13+:** System Settings → Privacy & Security → Accessibility → add Terminal, then Automation → allow Terminal to control your browsers.
- **macOS 10.15–12:** System Preferences → Security & Privacy → Privacy → Accessibility → add Terminal, then Automation → allow Terminal to control your browsers.

> `install.sh` will offer to open the correct settings screen for you.

### 4. OpenAI API key *(optional)*

Skip this if you don't have one — Vigil will still send a plain visit list digest generated entirely on your machine.

For the full AI summary (categories, timeline highlights, flagged content analysis):
1. Sign up at [platform.openai.com](https://platform.openai.com)
2. Create an API key under **API Keys**
3. Enable billing (~$0.001 per digest)

### 5. Email account (SMTP)

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

### 6. Settings reference

`install.sh` will prompt for everything interactively. To configure manually:

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
| `SUMMARY_SCHEDULE` | `daily` | `hourly` / `daily` / `weekly` / `monthly` |
| `SUMMARY_SCHEDULE_HOUR` | `21` | Hour to send (0–23) |
| `SUMMARY_SCHEDULE_MINUTE` | `0` | Minute to send (0–59) |
| `SUMMARY_SCHEDULE_WEEKDAY` | `mon` | `mon`–`sun` (weekly only) |
| `SUMMARY_SCHEDULE_DAY` | `1` | Day 1–28 (monthly only) |
| `ALERT_ENABLED` | `true` | Enable/disable instant alerts |
| `ALERT_EMAIL` | `true` | Send alert via email |
| `ALERT_COOLDOWN_MINUTES` | `30` | Minutes before the same domain alerts again |

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

```bash
bash install.sh
```

The installer will:
1. Verify macOS version, Python, and required files
2. Prompt for any missing credentials
3. Validate your SMTP connection (and OpenAI key if provided)
4. Open macOS privacy settings for browser access
5. Install Python packages
6. Start both background services (auto-restart on crash, auto-start on login)
7. Send a confirmation email

```bash
bash install.sh --status   # check if services are running
bash install.sh            # re-run any time to update settings or restart services
```

**After installation, Vigil runs silently:**
- **Every ~5 seconds** — logs the active browser tab and time spent
- **Instantly** — sends an alert if a pornographic site is detected
- **On schedule** — sends your digest (AI summary or plain visit list)
- **Every 5 minutes** — checks the tracker is running; alerts if it stops

---

## 🛑 Uninstall

```bash
bash uninstall.sh
```

You'll be asked whether to also delete your log files and settings.

---

## 📄 Log Files

All logs are stored in `~/Library/Logs/Vigil/` and `~/Library/Application Support/Vigil/`.

| File | Contents |
|---|---|
| `tracker_daemon.log` | Tracker start/stop events and polled URLs |
| `tracker_stderr.log` | Tracker errors |
| `summarizer_daemon.log` | Digest sends, API calls, watchdog checks |
| `summarizer_stderr.log` | Summariser errors |
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

```bash
.venv/bin/pytest tests/ -v
```

No real browsing data, email accounts, or OpenAI calls are used — everything runs against temporary files.

| File | What is tested |
|---|---|
| `test_tracker.py` | Log writing, timestamps, hash updates, session detection, shutdown events |
| `test_summarizer.py` | Log cleanup, domain parsing, time totals, email formatting, tamper detection |

---

## 🔧 Tech Stack

| Component | Tool |
|---|---|
| Browser watching | AppleScript via Python `subprocess` |
| Scheduling | APScheduler |
| AI summaries | OpenAI Python SDK *(optional)* |
| Email | Python `smtplib` |
| Background services | macOS launchd |

---

## 📚 References

- *Pornography — Harmless or Toxic?* — [jw.org](https://www.jw.org/en/library/magazines/wp20130801/is-pornography-harmful/)
- *Pornography — A Dangerous Trap* — [jw.org](https://www.jw.org/en/library/magazines/watchtower-study-june-2019/pornography-a-dangerous-trap/)
