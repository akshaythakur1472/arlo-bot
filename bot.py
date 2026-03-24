"""
Telegram Reminder Bot — Snarky + Supportive + Persistent
Stack: python-telegram-bot, APScheduler, SQLite, Groq API
"""

import os
import logging
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from db import init_db
from reminders import (
    parse_and_save_reminder,
    mark_done,
    get_due_reminders,
    snooze_reminder,
    get_all_active,
    delete_reminder
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
YOUR_CHAT_ID = int(os.environ["YOUR_CHAT_ID"])  # Only you can use this bot

scheduler = AsyncIOScheduler()


# ── Auth guard ─────────────────────────────────────────────────────────────────
def authorized(update: Update) -> bool:
    return update.effective_chat.id == YOUR_CHAT_ID


# ── Handlers ───────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.reply_text(
        "Hey! I'm your personal nag-bot 🫵\n\n"
        "Just tell me what to remind you — in plain English.\n\n"
        "Examples:\n"
        "• *remind me to call mom tonight*\n"
        "• *remind me to submit report by Friday*\n"
        "• *remind me to drink water every 2 hours*\n\n"
        "When you're done with something, just say *done* or *yes done*.\n"
        "Use /list to see active reminders.",
        parse_mode="Markdown"
    )


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    lower = text.lower()

    # Done signals
    done_phrases = ["done", "yes done", "i did it", "finished", "completed", "i have done it", "yep done", "yeah done"]
    if any(p in lower for p in done_phrases):
        result = await mark_done(chat_id, text)
        await update.message.reply_text(result)
        return

    # Snooze signals
    if "snooze" in lower or "remind me later" in lower or "not now" in lower:
        result = await snooze_reminder(chat_id)
        await update.message.reply_text(result)
        return

    # Otherwise treat as a new reminder
    response = await parse_and_save_reminder(chat_id, text)
    await update.message.reply_text(response, parse_mode="Markdown")


async def list_reminders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    reminders = await get_all_active(update.effective_chat.id)
    if not reminders:
        await update.message.reply_text("You have no active reminders. Living dangerously, I see. 😏")
        return
    lines = ["📋 *Your active reminders:*\n"]
    for i, r in enumerate(reminders, 1):
        deadline = r['deadline'] if r['deadline'] else "no deadline"
        lines.append(f"{i}. {r['task']} _(due: {deadline})_ — ID: `{r['id']}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def delete_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /delete <reminder_id>")
        return
    result = await delete_reminder(update.effective_chat.id, ctx.args[0])
    await update.message.reply_text(result)


# ── Scheduler job: check and nudge ─────────────────────────────────────────────
async def nudge_job(app: Application):
    nudges = await get_due_reminders()
    for nudge in nudges:
        try:
            await app.bot.send_message(
                chat_id=nudge["chat_id"],
                text=nudge["message"],
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send nudge: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    init_db()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Nudge every 30 minutes
    scheduler.add_job(nudge_job, "interval", minutes=30, args=[app])
    scheduler.start()

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
