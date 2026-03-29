"""
Arlo — Telegram Reminder Bot
Smart intent detection, priority conflict resolution, natural English understanding.
Scheduled: 9am morning briefing, 9pm evening check-in, 7pm Sunday weekly preview.
"""
from __future__ import annotations

import os
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from db import init_db
from reminders import (
    detect_intent_and_respond,
    get_due_reminders,
    get_all_active,
    delete_reminder,
    snooze_reminder,
    generate_morning_briefing,
    generate_evening_checkin,
    generate_weekly_preview,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
YOUR_CHAT_ID = int(os.environ["YOUR_CHAT_ID"])

scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")


def authorized(update: Update) -> bool:
    return update.effective_chat.id == YOUR_CHAT_ID


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.reply_text(
        "Hey! I'm Arlo — your personal nag 🫵\n\n"
        "Just talk to me naturally:\n\n"
        "• *remind me to call mom tonight*\n"
        "• *I drank water*\n"
        "• *fill timesheet by 6pm and call friend at 5pm*\n"
        "• *every monday at 9am review goals*\n"
        "• *not yet, give me 5 mins*\n"
        "• *what do I have today*\n\n"
        "I'll figure out what you mean.\n"
        "You'll get a morning briefing at 9am, evening check-in at 9pm, and a weekly preview every Sunday at 7pm.\n\n"
        "Use /list to see all reminders.",
        parse_mode="Markdown"
    )


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    response = await detect_intent_and_respond(chat_id, text)
    await update.message.reply_text(response, parse_mode="Markdown")


async def list_reminders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    reminders = await get_all_active(update.effective_chat.id)
    if not reminders:
        await update.message.reply_text("No active reminders. Living dangerously, I see. 😏")
        return
    lines = ["📋 *Your active reminders:*\n"]
    for i, r in enumerate(reminders, 1):
        deadline = r['deadline'] if r['deadline'] else "no deadline"
        priority_label = {1: "🔴", 2: "🟡", 3: "🟢"}.get(r.get('priority', 2), "🟡")
        lines.append(f"{priority_label} {i}. {r['task']} _(due: {deadline})_ — ID: `{r['id']}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def delete_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /delete <reminder_id>")
        return
    result = await delete_reminder(update.effective_chat.id, ctx.args[0])
    await update.message.reply_text(result)


# ── Scheduled jobs ────────────────────────────────────────────────────────────

async def nudge_job(app: Application):
    nudges = await get_due_reminders()
    for nudge in nudges:
        try:
            await app.bot.send_message(chat_id=nudge["chat_id"], text=nudge["message"], parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send nudge: {e}")


async def morning_briefing_job(app: Application):
    try:
        msg = await generate_morning_briefing(YOUR_CHAT_ID)
        if msg:
            await app.bot.send_message(chat_id=YOUR_CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Morning briefing failed: {e}")


async def evening_checkin_job(app: Application):
    try:
        msg = await generate_evening_checkin(YOUR_CHAT_ID)
        if msg:
            await app.bot.send_message(chat_id=YOUR_CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Evening check-in failed: {e}")


async def weekly_preview_job(app: Application):
    try:
        msg = await generate_weekly_preview(YOUR_CHAT_ID)
        if msg:
            await app.bot.send_message(chat_id=YOUR_CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Weekly preview failed: {e}")


# ── Startup ───────────────────────────────────────────────────────────────────

async def post_init(app: Application):
    # Nudge check every minute
    scheduler.add_job(nudge_job, "interval", minutes=1, args=[app])

    # 9am IST daily morning briefing
    scheduler.add_job(morning_briefing_job, CronTrigger(hour=9, minute=0, timezone="Asia/Kolkata"), args=[app])

    # 9pm IST daily evening check-in
    scheduler.add_job(evening_checkin_job, CronTrigger(hour=21, minute=0, timezone="Asia/Kolkata"), args=[app])

    # 7pm IST every Sunday weekly preview
    scheduler.add_job(weekly_preview_job, CronTrigger(day_of_week="sun", hour=19, minute=0, timezone="Asia/Kolkata"), args=[app])

    scheduler.start()
    logger.info("Scheduler started — morning briefing 9am, evening check-in 9pm, weekly preview Sunday 7pm IST.")


def main():
    init_db()

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Arlo is running...")
    app.run_polling()


if __name__ == "__main__":
    main()