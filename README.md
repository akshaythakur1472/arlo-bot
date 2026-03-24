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

### Step 4 — Deploy to Render.com (free)

1. Push this folder to a GitHub repo (can be private)
2. Go to https://render.com and sign up
3. Click **New → Blueprint** and connect your GitHub repo
4. Render reads `render.yaml` automatically
5. Set the 3 environment variables when prompted:
   - `TELEGRAM_TOKEN` → your bot token from Step 1
   - `GROQ_API_KEY` → your Groq key from Step 3
   - `YOUR_CHAT_ID` → your chat ID from Step 2
6. Click Deploy

That's it. Your bot will be running 24/7 for free.

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
