# 🤖 Arlo — Your Personal Nag Bot

A Telegram reminder bot that actually follows up until you're done.
Powered by Groq (free) + Render.com (free hosting).

---

## Setup (takes ~20 minutes)

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

### Step 4 — Deploy to EC2

#### 4a. Launch the instance
1. Go to AWS Console → EC2 → **Launch Instance**
2. Choose **Amazon Linux 2023**
3. Instance type: **t2.micro** (free tier)
4. Key pair: **Proceed without a key pair**
5. Firewall → **Create security group** → uncheck everything → Add rule:
   - Type: SSH, Source: Custom, IP: `3.0.5.32/29` _(AWS Instance Connect IP for ap-south-1)_
6. Launch instance

#### 4b. Connect via browser terminal
1. EC2 → Instances → select your instance
2. Click **Connect → EC2 Instance Connect → Connect**
3. Browser terminal opens — no SSH key needed

#### 4c. Install dependencies
```bash
sudo yum update -y
sudo yum install python3-pip git -y

git clone https://github.com/your-username/arlo-bot.git
cd arlo-bot

python3 -m venv venv
source venv/bin/activate
pip install python-telegram-bot apscheduler groq httpx==0.27.2
```

#### 4d. Set up systemd service (runs 24/7, survives reboots)
```bash
sudo nano /etc/systemd/system/arlo.service
```

Paste this — replace ALL three values with your actual keys:
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

#### 4e. Useful commands
```bash
# Watch live logs
sudo journalctl -u arlo -f

# Update after code changes
cd ~/arlo-bot && git pull && sudo systemctl restart arlo

# Restart bot
sudo systemctl restart arlo
```

---

## How to use

Just message your bot in plain English:

| You say | What happens |
|---|---|
| `remind me to call dentist tonight` | Reminds at 8pm, then every hour |
| `remind me to submit report by Friday` | Nudges immediately + escalates near deadline |
| `remind me to drink water every 2 hours` | Recurring every 2 hours |
| `done` / `yes done` / `i did it` | Marks the latest active reminder complete |
| `not now` / `snooze` | Snoozes for 90 minutes |
| `/list` | Shows all active reminders |
| `/delete 3` | Deletes reminder with ID 3 |

---

## Cost

| Service | Cost |
|---|---|
| Telegram Bot API | Free forever |
| Groq API (Llama 3 70B) | Free tier (very generous for personal use) |
| Render.com (Worker) | Free tier |
| **Total** | **$0** |

---

## Files

```
bot.py          — Main bot, command handlers, scheduler
reminders.py    — AI parsing, nudge generation, DB operations
db.py           — SQLite schema
requirements.txt
render.yaml     — Render deployment config
```
