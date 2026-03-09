# Vigil

> **🧪 Experimental** — this is an early-stage project. Things may break. Please report any issues you find.

> **⚠️ macOS only for now** — Windows and Linux support is coming.

Vigil runs quietly in the background on your Mac. It watches which websites you visit — in both normal and private/incognito browser windows — and emails a summary to you and a trusted friend on a schedule you pick. If a pornographic site is opened, an alert email goes out straight away.

---

## 🎯 Why This Exists

Vigil is for people who want to break a pornography habit. It brings your browsing into the open — so you're not fighting it alone, in secret.

### Why the name "Vigil"

A vigil means keeping watch — staying alert through the night so nothing slips by. That's what this tool does. It watches quietly in the background so you don't have to face this alone. The name also reflects the role of your accountability partner — not a warden, but a trusted person who is *with you* in the fight.

### Why porn is so hard to quit

Most people try to quit by willpower alone. It rarely works. Here's why:

- **Winning once doesn't mean it's over.** Even people who have successfully stopped watching pornography can find that the memories come back without warning. Old images can pop into the mind at any time and create a sudden, strong urge to relapse. As one source puts it: *"Pornographic images or thoughts can stay in a person's mind for a long time. They can reappear without warning."* Staying accountable — even after a period of success — is what keeps those moments from turning back into a habit.
- **It changes how your brain works.** Pornography floods the brain with dopamine — the chemical that makes things feel good. Over time the brain gets wired to crave it, just like a drug. Images can leave a lasting imprint that is very hard to erase.
- **Hiding it makes it stronger.** Most people keep the habit secret out of shame. But secrecy is what keeps it alive. The more hidden it stays, the harder it is to break.
- **Shame pulls you back in.** After a slip, many people feel guilty and worthless. Those feelings don't help — they often push people right back to pornography as a way to feel better, creating a cycle.
- **You can't beat it alone.** One person put it simply: *"You think you can beat it by yourself. But that is not true. You can beat it only with help."*

### How Vigil helps

- **It removes the hiding place — even in private mode.** Vigil tracks both normal and private/incognito browsing. There are no blind spots. When you know someone will see your activity, the private space where the habit lives disappears.
- **It turns shame into a conversation.** When your partner already knows — because Vigil told them — a slip becomes something you talk about together, not something you carry alone.
- **It acts fast.** Most tools only show you what happened yesterday. Vigil sends your partner an alert the moment a pornographic site is opened. That means a slip can be caught and talked about right away, before it turns into a longer session.
- **It shows your progress clearly.** The regular digest shows how often slips happen, whether things are getting better, and when temptation tends to hit. Seeing that in writing is hard to argue with.

### Built on consent

> **This only works if you set it up yourself.** Vigil must be installed by the person being monitored — no one else. Putting it on someone's computer without their knowledge is a serious breach of trust and is likely illegal. This is a tool for mutual support, not spying.

You choose to install it. You choose your partner. You can remove it any time. Accountability you choose for yourself works far better than accountability forced on you.

### How it works

#### Step 1 — Install it (takes about 5 minutes)

Run one command in Terminal:

```bash
bash install.sh
```

The installer walks you through everything interactively:

1. **Checks your Mac** — confirms you have a supported macOS version and Python installed.
2. **Asks for your credentials** — your OpenAI API key, your email account details, and who should receive the reports. You type them in when prompted. Nothing is hardcoded.
3. **Tests everything before going live** — it connects to OpenAI and your email provider to make sure your credentials actually work before installing anything.
4. **Asks you to grant browser access** — Vigil needs your permission to read your browser tabs (this is what lets it see private/incognito windows). The installer offers to open the right settings screen for you.
5. **Installs Python packages** — everything Vigil needs to run.
6. **Starts both background services** — the tracker and the summariser are registered with macOS so they start automatically when you log in and restart themselves if they ever crash.
7. **Sends a confirmation email** — you and your accountability partner get a test email confirming everything is set up and working.

That's it. You don't need to leave a terminal window open. Vigil runs in the background from this point on.

#### Step 2 — What happens after installation

Once installed, Vigil runs silently in the background. Here's what it does:

- **Every few seconds** — checks which tab is active in your browser and logs the site and how long you spent on it. This works in both normal and private/incognito windows.
- **The moment a pornographic site is detected** — an alert email is sent immediately to everyone in your recipients list. Your accountability partner doesn't have to wait for the next report. The same site won't trigger another alert for 30 minutes (to avoid a flood of emails).
- **On your chosen schedule** (daily, weekly, etc.) — OpenAI reads your browsing log and writes a plain-English summary: which sites you visited, how long you spent, categories, and a clear flag if any pornographic content was detected. That summary is emailed to you and your partner.
- **Every 5 minutes** — Vigil checks that the tracker is still running. If it has been stopped for any reason, an alert email goes out immediately. This means your partner will know if tracking stops unexpectedly.
- **If the log file is tampered with** — Vigil detects the change and sends an alert before the next summary is sent.

#### Step 3 — Talk about it

When your partner receives a report or alert, you talk about it — honestly and without judgment. That conversation is the whole point. Vigil gives you both the facts. What you do with them is up to you.

---

## ✨ Features

- 🌐 **All major browsers** — sees full URLs in Safari, Chrome, Edge, Brave, and Arc, including private/incognito windows. Falls back to page title for Firefox, Opera, and Tor Browser. See [Supported Browsers](#-supported-browsers).
- 🚨 **Instant alert emails** — the moment a pornographic site is detected, an alert email is sent. No waiting for the next summary. A cooldown prevents repeat alerts for the same site.
- 🤖 **AI summary** — OpenAI reads your browsing log and writes a plain-English digest: top sites, categories, time spent, and any pornographic content flagged clearly.
- 📧 **Email reports** — clean HTML email sent via your own email account (Gmail, Outlook, iCloud, etc.). No third-party service needed.
- ⏰ **Flexible schedule** — send reports hourly, daily, weekly, or monthly. You control the timing.
- 🚀 **Always running** — Vigil starts automatically when you log in and restarts itself if it crashes.
- 🛡️ **Tamper detection** — if the browsing log is edited, Vigil notices and sends an alert before the next summary goes out.
- 👁️ **Watchdog** — if the tracking service is stopped, an alert email is sent immediately.
- 🔐 **Private AI** — only website names (e.g. `youtube.com`) are sent to OpenAI. Full URLs never leave your computer.

---

## 🌐 Supported Browsers

| Browser | Tracks full URL | Private / Incognito |
|---|---|---|
| Safari | ✅ Yes | ✅ Yes |
| Google Chrome | ✅ Yes | ✅ Yes |
| Microsoft Edge | ✅ Yes | ✅ Yes |
| Brave | ✅ Yes | ✅ Yes |
| Arc | ✅ Yes | ✅ Yes |
| Firefox | ⚠️ Page title only | ⚠️ Title only |
| Opera | ⚠️ Page title only | ⚠️ Title only |
| Tor Browser | ⚠️ Page title only | ⚠️ Title only |

> Browsers marked ⚠️ are still tracked, but with less detail. For best results, use one of the ✅ browsers.

---

## 📁 Project Structure

```
personal_tracker/
├── tracker.py                  # Watches your browser tabs every few seconds
├── summarizer.py               # Sends the scheduled digest emails
├── alerter.py                  # Sends instant alerts when a porn site is detected
├── config.py                   # Reads settings from your .env file
├── requirements.txt            # Python packages needed
├── com.vigil.tracker.plist     # Keeps tracker.py running in the background
├── com.vigil.summarizer.plist  # Keeps summarizer.py running in the background
├── install.sh                  # One-command setup
├── uninstall.sh                # One-command removal
├── .env.template               # Settings template — copy to .env and fill in
├── data/
│   └── adult_domains.txt       # List of known porn sites used for instant alerts
└── tests/
    ├── conftest.py
    ├── test_tracker.py
    └── test_summarizer.py
```

---

## ✅ Before You Install

You need all of the following before running `install.sh`.

### 1. macOS version

| macOS Version | Supported |
|---|---|
| 15 Sequoia | ✅ Yes |
| 14 Sonoma | ✅ Yes |
| 13 Ventura | ✅ Yes |
| 12 Monterey | ✅ Yes |
| 11 Big Sur | ✅ Yes |
| 10.15 Catalina | ✅ Yes (minimum) |
| 10.14 Mojave or older | ❌ No |

### 2. Python 3.8 or newer

```bash
python3 --version
```

If you don't have it, install via Homebrew:

```bash
brew install python
```

### 3. Allow browser access

Vigil uses macOS Automation to read your browser tabs (including private windows). You need to allow this in your Mac's settings.

**macOS 13 and later (System Settings):**
- System Settings → Privacy & Security → Accessibility → add Terminal
- System Settings → Privacy & Security → Automation → allow Terminal to control your browsers

**macOS 10.15–12 (System Preferences):**
- System Preferences → Security & Privacy → Privacy → Accessibility → add Terminal
- System Preferences → Security & Privacy → Privacy → Automation → allow Terminal to control your browsers

### 4. OpenAI API key

1. Sign up at platform.openai.com
2. Create an API key under **API Keys**
3. Make sure billing is enabled (costs roughly $0.001 per digest)

### 5. Email account (SMTP)

Vigil sends emails using your existing email account. No extra service needed.

| Provider | SMTP address | Port |
|---|---|---|
| Gmail | `smtp.gmail.com` | 587 |
| Outlook / Microsoft 365 | `smtp.office365.com` | 587 |
| Fastmail | `smtp.fastmail.com` | 587 |
| Apple iCloud | `smtp.mail.me.com` | 587 |

> **Gmail users:** you need an App Password, not your normal password. Go to myaccount.google.com/apppasswords to create one. You'll need 2-Step Verification enabled first.

### 6. Fill in your settings

`install.sh` will ask you for all of this interactively. Or do it manually:

```bash
cp .env.template .env
```

| Setting | What it does |
|---|---|
| `OPENAI_API_KEY` | Your OpenAI key |
| `OPENAI_MODEL` | AI model to use (default: `gpt-4o-mini`) |
| `SMTP_HOST` | Your email provider's SMTP address |
| `SMTP_PORT` | Port — usually `587` |
| `SMTP_USER` | Your email address |
| `SMTP_PASS` | Your email password or app password |
| `SMTP_FROM` | Who the email is from (defaults to `SMTP_USER`) |
| `SMTP_TO` | Who gets the emails — separate multiple addresses with a comma |
| `SUMMARY_SCHEDULE` | `hourly` / `daily` / `weekly` / `monthly` |
| `SUMMARY_SCHEDULE_HOUR` | Hour to send (0–23, default `21`) |
| `SUMMARY_SCHEDULE_MINUTE` | Minute to send (0–59, default `0`) |
| `SUMMARY_SCHEDULE_WEEKDAY` | For weekly: `mon`–`sun` (default `mon`) |
| `SUMMARY_SCHEDULE_DAY` | For monthly: day 1–28 (default `1`) |
| `ADULT_ALERT_ENABLED` | Turn instant alerts on/off (`true` / `false`, default `true`) |
| `ADULT_ALERT_EMAIL` | Send alert emails (`true` / `false`, default `true`) |
| `ADULT_ALERT_COOLDOWN_MINUTES` | How long before the same site can trigger another alert (default `30`) |

> **Tip:** Put both your email and your partner's email in `SMTP_TO` separated by a comma. Both will get every digest and every instant alert.

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

This will:
1. Check that everything is in place (macOS version, Python, required files)
2. Ask for any missing credentials
3. Test your OpenAI key and email settings before doing anything
4. Prompt you to grant the required macOS permissions (and offer to open Settings for you)
5. Install Python packages
6. Start both background services
7. Send a test email to confirm it's all working

> Already installed? Run `bash install.sh` again any time to update your settings or restart the services.

### Check if it's running

```bash
bash install.sh --status
```

---

## 🛑 Uninstall

```bash
bash uninstall.sh
```

You'll be asked whether to also delete your log files and settings.

---

## 📄 Log Files

| File | What's in it |
|---|---|
| `tracker_daemon.log` | When the tracker started, stopped, and what it polled |
| `tracker_stderr.log` | Tracker error messages |
| `summarizer_daemon.log` | When emails were sent, OpenAI calls, watchdog checks |
| `summarizer_stderr.log` | Summariser error messages |
| `detailed_activity_log.txt` | Full browsing log with timestamps |
| `detailed_activity_log.txt.sha256` | Tamper-detection file (auto-generated) |

---

## 🔐 Privacy

- **Stored on your computer:** Full URLs with timestamps. Never leaves your machine.
- **Sent to OpenAI:** Website names only (e.g. `youtube.com`). No full URLs, no page titles, no search terms.
- **Sent by email:** The AI-written summary, which includes site names and categories.
- **Tamper detection:** If anyone edits the log file, Vigil detects it and sends an alert before the next digest.
- **Watchdog:** If the tracker is stopped, an alert email goes out immediately.

> Keep your `.env` file private. Do not commit it to version control — it's already in `.gitignore`.

---

## 🧪 Tests

```bash
.venv/bin/pytest tests/ -v
```

No real browsing data, email accounts, or OpenAI calls are used in tests — everything runs against temporary files.

| File | What is tested |
|---|---|
| `tracker.py` | Log writing, timestamps, hash updates, session detection, shutdown events, AppleScript generation |
| `summarizer.py` | Log cleanup, domain parsing, time totals, email formatting, tamper detection, digest scheduling |

---

## 🔧 Tech Stack

| Part | Tool |
|---|---|
| Browser watching | AppleScript via Python `subprocess` |
| Scheduling | APScheduler |
| AI summaries | OpenAI Python SDK |
| Email | Python `smtplib` (built-in, no extra package) |
| Background service | macOS launchd |

---

## 📚 References

- *Pornography — Harmless or Toxic?* — jw.org: https://www.jw.org/en/library/magazines/wp20130801/is-pornography-harmful/
- *Pornography — A Dangerous Trap* — jw.org: https://www.jw.org/en/library/magazines/watchtower-study-june-2019/pornography-a-dangerous-trap/
- OpenAI Platform: https://platform.openai.com
- APScheduler docs: https://apscheduler.readthedocs.io/
- OpenAI Python SDK: https://github.com/openai/openai-python
- Homebrew: https://brew.sh
- Gmail App Passwords: https://myaccount.google.com/apppasswords

