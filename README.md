# 🤖 Arlo — Your Personal Nag Bot

A Telegram reminder bot that actually follows up until you're done.
Powered by Groq (free) + EC2 or local machine.

---

## Setup

### Step 1 — Create your Telegram Bot

1. Open Telegram, search for **@BotFather**
2. Send `/newbot`
3. Give it a name (e.g. "Arlo") and a username (e.g. `my_arlo_bot`)
4. BotFather gives you a **token** — save it. Looks like: `7123456789:AAFxxx...`

### Step 2 — Get your Telegram Chat ID

1. Search for **@userinfobot** on Telegram
2. Send it any message
3. It replies with your **Id** — save that number

### Step 3 — Get a free Groq API key

1. Go to https://console.groq.com
2. Sign up (free, no credit card)
3. Create an API key — save it

---

### Step 4 — Run locally (for testing)

Good for quickly testing before deploying to EC2.

```bash
# Clone the repo
git clone https://github.com/your-username/arlo-bot.git
cd arlo-bot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate        # Mac/Linux
# venv\Scripts\activate         # Windows

# Install dependencies
pip install python-telegram-bot apscheduler groq httpx==0.27.2

# Set environment variables — Mac/Linux
export TELEGRAM_TOKEN="your-actual-token"
export GROQ_API_KEY="your-actual-groq-key"
export YOUR_CHAT_ID="your-actual-numeric-id"

# Windows
# set TELEGRAM_TOKEN=your-actual-token
# set GROQ_API_KEY=your-actual-groq-key
# set YOUR_CHAT_ID=your-actual-numeric-id

# Run
python3 bot.py
```

Bot runs as long as the terminal is open. `Ctrl+C` to stop.

---

### Step 5 — Deploy to EC2 (runs 24/7)

#### 5a. Launch the instance
1. Go to AWS Console → EC2 → **Launch Instance**
2. Choose **Amazon Linux 2023**
3. Instance type: **t2.micro** (free tier)
4. Key pair: **Proceed without a key pair**
5. Firewall → **Create security group** → uncheck everything → Add rule:
   - Type: SSH, Source: Custom, IP: `3.0.5.32/29` _(AWS Instance Connect IP for ap-south-1)_
6. Launch instance

#### 5b. Connect via browser terminal
1. EC2 → Instances → select your instance
2. Click **Connect → EC2 Instance Connect → Connect**
3. Browser terminal opens — no SSH key needed

#### 5c. Install dependencies
```bash
sudo yum update -y
sudo yum install python3-pip git -y

git clone https://github.com/your-username/arlo-bot.git
cd arlo-bot

python3 -m venv venv
source venv/bin/activate
pip install python-telegram-bot apscheduler groq httpx==0.27.2
```

#### 5d. Set up systemd service (runs 24/7, survives reboots)
```bash
sudo nano /etc/systemd/system/arlo.service
```

Paste this — **replace ALL three values** with your actual keys:
```ini
[Unit]
Description=Arlo Reminder Bot
After=network.target

[Service]
User=root
WorkingDirectory=/home/ec2-user/arlo-bot
ExecStart=/home/ec2-user/arlo-bot/venv/bin/python3 /home/ec2-user/arlo-bot/bot.py
Restart=always
RestartSec=10
Environment=TELEGRAM_TOKEN=your-actual-token-here
Environment=GROQ_API_KEY=your-actual-groq-key-here
Environment=YOUR_CHAT_ID=your-actual-numeric-id-here

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable arlo
sudo systemctl start arlo
sudo systemctl status arlo
```

Should show `active (running)`. Done!

#### 5e. Useful commands
```bash
# Watch live logs
sudo journalctl -u arlo -f

# Update after pushing code changes to GitHub
cd ~/arlo-bot && git pull && sudo systemctl restart arlo

# Restart bot
sudo systemctl restart arlo

# Check status
sudo systemctl status arlo
```

---

## How to use

Just message your bot in plain English:

| You say | What happens |
|---|---|
| `remind me to call dentist tonight` | Reminds at 8pm, then every hour |
| `remind me to submit report by Friday` | Nudges immediately + escalates near deadline |
| `remind me to drink water every 2 hours` | Recurring every 2 hours |
| `remind me every monday at 9am to review goals` | Fires every Monday at 9am |
| `remind me every other friday at 6pm to call mom` | Fires every other Friday at 6pm |
| `done` / `yes done` / `i did it` | Marks the latest active reminder complete |
| `not now` / `snooze` | Snoozes for 90 minutes |
| `/list` | Shows all active reminders |
| `/delete 3` | Deletes reminder with ID 3 |

---

## Cost

| Service | Cost |
|---|---|
| Telegram Bot API | Free forever |
| Groq API (Llama 3.3 70B) | Free tier (generous for personal use) |
| AWS EC2 t2.micro | Free for 12 months, ~$8/mo after |
| **Total** | **$0 to start** |

---

## Files

```
bot.py          — Main bot, command handlers, scheduler
reminders.py    — AI parsing, nudge generation, DB operations
db.py           — SQLite schema
requirements.txt
render.yaml     — Render deployment config (alternative to EC2)
```