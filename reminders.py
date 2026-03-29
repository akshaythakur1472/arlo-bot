"""
Arlo — reminders.py
Natural language intent detection, duplicate detection,
priority conflict resolution, cron scheduling, nudge personality,
morning briefing, evening check-in, weekly preview.
"""
from __future__ import annotations

import os
import time
os.environ["TZ"] = "Asia/Kolkata"
time.tzset()

import re
import json
import logging
from datetime import datetime, timedelta
from groq import Groq
from db import get_conn

logger = logging.getLogger(__name__)
groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
MODEL = "llama-3.3-70b-versatile"

DAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6
}

SYSTEM_PROMPT = """You are Arlo — a smart, friendly personal assistant who helps the user stay on top of tasks.
Your personality:
- Caring, supportive coach who genuinely wants the user to succeed
- Snarky best friend who lovingly teases about procrastinating
- No-nonsense when needed
- You KNOW this user is a chronic procrastinator — factor that into every response

Keep messages SHORT (1-3 sentences). Use emojis sparingly. Never be mean — always caring."""


# ── Time helpers ──────────────────────────────────────────────────────────────

def _extract_time(text: str) -> tuple[int, int]:
    match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text)
    if not match:
        return 9, 0
    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    period = match.group(3)
    if period == "pm" and hour != 12:
        hour += 12
    elif period == "am" and hour == 12:
        hour = 0
    return hour, minute


def _extract_snooze_minutes(text: str) -> int:
    """Extract snooze duration from natural language. Returns minutes."""
    lower = text.lower()

    # "not yet" / "in a bit" / "almost" / vague = 15 mins (we know them)
    vague_phrases = ["not yet", "in a bit", "almost", "nearly", "almost done", "give me a sec", "one sec", "just a sec"]
    if any(p in lower for p in vague_phrases):
        return 15

    # Extract number + unit
    match = re.search(r'(\d+)\s*(min|mins|minute|minutes|hr|hrs|hour|hours|sec|secs|second|seconds)?', lower)
    if match:
        n = int(match.group(1))
        unit = match.group(2) or "min"
        if "hour" in unit or unit in ("hr", "hrs"):
            return n * 60
        if "sec" in unit:
            return max(1, n // 60) or 1
        return n  # minutes

    return 15  # default for vague requests


def next_weekday(weekday: int, at_hour: int, at_minute: int, every_other: bool = False, last_fire: datetime | None = None) -> datetime:
    now = datetime.now()
    days_ahead = weekday - now.weekday()
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0:
        candidate = now.replace(hour=at_hour, minute=at_minute, second=0, microsecond=0)
        if candidate <= now:
            days_ahead = 7
    next_dt = (now + timedelta(days=days_ahead)).replace(hour=at_hour, minute=at_minute, second=0, microsecond=0)
    if every_other and last_fire:
        days_since = (next_dt - last_fire).days
        if days_since < 10:
            next_dt += timedelta(weeks=1)
    return next_dt


def _next_cron_occurrence(cron_expr: str, every_other: bool, last_fire: datetime | None) -> datetime:
    parts = cron_expr.split(":")
    if parts[0] == "weekly":
        _, day_num, hour, minute = parts
        return next_weekday(int(day_num), int(hour), int(minute), every_other=every_other, last_fire=last_fire)
    elif parts[0] == "weekdays":
        _, hour, minute = parts
        now = datetime.now()
        for i in range(1, 8):
            candidate = now + timedelta(days=i)
            if candidate.weekday() < 5:
                return candidate.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    return datetime.now() + timedelta(hours=24)


def parse_cron_from_text(text: str) -> dict | None:
    lower = text.lower()
    every_other = "every other" in lower
    found_day = None
    for day_name, day_num in DAY_MAP.items():
        if day_name in lower:
            found_day = (day_name, day_num)
            break
    if "weekday" in lower and not found_day:
        hour, minute = _extract_time(lower)
        return {"type": "weekdays", "every_other": False, "hour": hour, "minute": minute}
    if not found_day:
        return None
    hour, minute = _extract_time(lower)
    return {"type": "weekly", "day_name": found_day[0], "day_num": found_day[1], "every_other": every_other, "hour": hour, "minute": minute}


# ── Core AI call ──────────────────────────────────────────────────────────────

async def _groq(prompt: str, temperature: float = 0.4, max_tokens: int = 600) -> str:
    response = groq_client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens
    )
    return response.choices[0].message.content.strip()


# ── Intent detection ──────────────────────────────────────────────────────────

async def detect_intent(chat_id: int, text: str) -> dict:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    active = await get_all_active(chat_id)
    active_summary = "\n".join([f"- ID {r['id']}: {r['task']} (due: {r['deadline'] or 'no deadline'})" for r in active]) or "none"

    prompt = f"""Current date/time (IST): {now_str}
User's active reminders:
{active_summary}

User message: "{text}"

You are parsing intent. This user is a known procrastinator.
CAREFULLY analyze the EXACT TENSE and PHRASING before deciding intent.

━━━ TENSE RULES (apply strictly) ━━━

1. PAST TENSE = mark_done
   Signals: "I kept", "I did", "I finished", "I called", "I drank", "I ate",
   "just did it", "already done", "I have done", "completed"
   → Mark matching existing reminder as done.
   → matched_reminder_id = ID of the most relevant existing reminder (or null if none match)

2. PRESENT CONTINUOUS + DURATION = set_reminder for FOLLOW-UP
   Signals: "I am [verb]ing X for Y mins/hours", "putting X for Y", "soaking X for Y",
   "charging X for Y", "keeping X for Y"
   Examples:
     "I am keeping coke in fridge for 30 min" → task = "take coke out of fridge", nudge in 30 mins
     "putting phone on charge for 1 hour" → task = "unplug phone", nudge in 60 mins
     "soaking dal for 2 hours" → task = "check the dal", nudge in 120 mins
   → intent = set_reminder, task = the FOLLOW-UP action, first_nudge = now + duration
   → DO NOT mark any existing reminder done

3. PRESENT NEED / FUTURE INTENT = set_reminder (new reminder, not follow-up)
   Signals: "I need to X", "remind me to X", "I should X", "I have to X",
   "I want to X", "don't forget X", "I need to keep coke in fridge"
   → intent = set_reminder, create new reminder for that task
   → DO NOT match to existing reminders even if topic is similar
   → matched_reminder_id = null always

4. SNOOZE signals = snooze
   Signals: "not yet", "wait", "give me X mins", "in a bit", "almost done",
   "5 more minutes", "not now", "later", "hold on", "one sec", "2 mins",
   "wait 2 mins", "not yet wait 2 mins", "almost", "soon", "just a minute"
   → snooze_minutes: extract number if given ("2 mins"→2, "half hour"→30), else 15

5. LIST = list
   Signals: "what do I have", "show reminders", "my list", "what's pending", "show tasks"

6. DELETE = delete
   Signals: "cancel X", "remove X", "delete X", "forget about X"

7. CHITCHAT = chitchat
   Signals: "thanks", "ok", "cool", "great", "👍", "nice", "okay", "alright"

━━━ CRITICAL RULE ━━━
"I need to keep coke in fridge" → set_reminder (NEED = future intent, NOT mark_done)
"I am keeping coke in fridge for 30 min" → set_reminder for follow-up (take it out)
"I kept coke in fridge" → mark_done (PAST TENSE)
Never confuse these three patterns.

━━━ RETURN JSON ━━━
{{
  "intent": "set_reminder" | "mark_done" | "snooze" | "list" | "delete" | "chitchat" | "unclear",
  "snooze_minutes": null or number,
  "tasks": [
    {{
      "task": "exact task to remind about (follow-up action for present-continuous)",
      "deadline_iso": "YYYY-MM-DDTHH:MM or null",
      "interval_minutes": null or number,
      "is_recurring": true/false,
      "is_cron": true/false,
      "first_nudge_iso": "YYYY-MM-DDTHH:MM",
      "priority": 1 (work/urgent) or 2 (medium) or 3 (personal/low),
      "task_type": "work" or "personal"
    }}
  ],
  "matched_reminder_id": null or ID (ONLY for mark_done/delete, NEVER for set_reminder),
  "chitchat_reply": null or short Arlo reply,
  "conflict_detected": true/false,
  "conflict_suggestion": null or string
}}

Timing (IST): "tonight"=8pm, "evening"=6pm, "morning"=9am, "tomorrow"=9am tomorrow
Work tasks=priority 1, personal=priority 3, conflict=deadlines within 2 hours of each other

Return ONLY the JSON. No explanation."""

    raw = await _groq(prompt, temperature=0.15, max_tokens=700)
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


# ── Duplicate check ───────────────────────────────────────────────────────────

async def find_duplicate(chat_id: int, task: str) -> dict | None:
    active = await get_all_active(chat_id)
    if not active:
        return None
    active_list = "\n".join([f"ID {r['id']}: {r['task']}" for r in active])
    prompt = f"""Active reminders:
{active_list}

New task to add: "{task}"

Is there an existing reminder that is CLEARLY about the exact same action?
Rules:
- "keep coke in fridge" and "take coke out of fridge" are NOT duplicates (different actions)
- "call dentist" and "call dentist at 5pm" ARE duplicates (same action)
- "drink water" and "drink water every 2 hours" ARE duplicates
- Only mark as duplicate if the action is genuinely identical, not just related

Return ONLY JSON: {{"duplicate": true/false, "id": null or matching ID, "existing_task": null or matching task text}}"""
    raw = await _groq(prompt, temperature=0.1, max_tokens=100)
    raw = raw.replace("```json", "").replace("```", "").strip()
    result = json.loads(raw)
    return result if result.get("duplicate") else None


# ── Save a reminder ───────────────────────────────────────────────────────────

async def save_reminder(chat_id: int, task_data: dict) -> int:
    cron_info = parse_cron_from_text(task_data.get("task", ""))
    cron_expr = None
    every_other = False
    first_nudge = task_data.get("first_nudge_iso")

    if cron_info or task_data.get("is_cron"):
        info = cron_info or {}
        day_num = info.get("day_num")
        hour = info.get("hour", 9)
        minute = info.get("minute", 0)
        every_other = info.get("every_other", False)
        if day_num is not None:
            next_dt = next_weekday(day_num, hour, minute, every_other=every_other)
            first_nudge = next_dt.strftime("%Y-%m-%dT%H:%M")
            cron_expr = f"weekly:{day_num}:{hour}:{minute}"
        elif info.get("type") == "weekdays":
            now = datetime.now()
            for i in range(1, 8):
                candidate = now + timedelta(days=i)
                if candidate.weekday() < 5:
                    first_nudge = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
                    break
            cron_expr = f"weekdays:{hour}:{minute}"

    conn = get_conn()
    cursor = conn.execute("""
        INSERT INTO reminders
            (chat_id, task, deadline, interval_minutes, next_nudge, is_recurring, cron_expression, cron_every_other, priority)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        chat_id,
        task_data.get("task"),
        task_data.get("deadline_iso"),
        task_data.get("interval_minutes", 60) if not cron_expr else None,
        first_nudge,
        1 if (task_data.get("is_recurring") or cron_expr) else 0,
        cron_expr,
        1 if every_other else 0,
        task_data.get("priority", 2),
    ))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id


# ── Main intent router ────────────────────────────────────────────────────────

async def detect_intent_and_respond(chat_id: int, text: str) -> str:
    try:
        intent_data = await detect_intent(chat_id, text)
    except Exception as e:
        logger.error(f"Intent detection failed: {e}")
        return "Hmm, my brain glitched. Try again? 🤔"

    intent = intent_data.get("intent")

    # ── Chitchat ──
    if intent == "chitchat":
        return intent_data.get("chitchat_reply") or "😄"

    # ── List ──
    if intent == "list":
        reminders = await get_all_active(chat_id)
        if not reminders:
            return "No active reminders. Living dangerously, I see. 😏"
        lines = ["📋 *Your active reminders:*\n"]
        for i, r in enumerate(reminders, 1):
            deadline = r['deadline'] if r['deadline'] else "no deadline"
            priority_label = {1: "🔴", 2: "🟡", 3: "🟢"}.get(r.get('priority', 2), "🟡")
            lines.append(f"{priority_label} {i}. {r['task']} _(due: {deadline})_ — ID: `{r['id']}`")
        return "\n".join(lines)

    # ── Snooze ──
    if intent == "snooze":
        snooze_minutes = intent_data.get("snooze_minutes") or _extract_snooze_minutes(text)
        return await snooze_reminder(chat_id, snooze_minutes=snooze_minutes, original_text=text)

    # ── Delete ──
    if intent == "delete":
        reminder_id = intent_data.get("matched_reminder_id")
        if reminder_id:
            return await delete_reminder(chat_id, str(reminder_id))
        return "Which reminder? Use /list to see IDs, then /delete <id>."

    # ── Mark done ──
    if intent == "mark_done":
        reminder_id = intent_data.get("matched_reminder_id")
        return await mark_done(chat_id, text, reminder_id=reminder_id)

    # ── Set reminder ──
    if intent == "set_reminder":
        tasks = intent_data.get("tasks", [])
        if not tasks:
            return "I didn't catch what you want to be reminded about. Can you rephrase?"

        conflict_msg = ""
        if intent_data.get("conflict_detected") and intent_data.get("conflict_suggestion"):
            conflict_msg = f"⚠️ *Heads up!* {intent_data['conflict_suggestion']}\n\n"

        replies = []
        for task_data in tasks:
            task_name = task_data.get("task", "")
            dup = await find_duplicate(chat_id, task_name)
            if dup:
                replies.append(f"You already have _{dup['existing_task']}_ (ID: `{dup['id']}`). Skipped duplicate. Use /delete {dup['id']} to replace it.")
                continue
            new_id = await save_reminder(chat_id, task_data)
            first_nudge = task_data.get("first_nudge_iso", "soon")
            try:
                nudge_dt = datetime.fromisoformat(first_nudge)
                nudge_str = nudge_dt.strftime("%A at %I:%M %p") if (nudge_dt - datetime.now()).days > 0 else nudge_dt.strftime("%I:%M %p today")
            except:
                nudge_str = "soon"
            priority_label = {1: "🔴 high", 2: "🟡 medium", 3: "🟢 low"}.get(task_data.get("priority", 2), "medium")
            replies.append(f"Got it — _{task_name}_ set ({priority_label} priority). First nudge: {nudge_str}.")

        return conflict_msg + "\n".join(replies)

    return "Not sure what you mean — try *remind me to call dentist tonight* or just tell me what you did."


# ── Nudge generation ──────────────────────────────────────────────────────────

async def generate_nudge(task: str, nudge_count: int, deadline: str | None, is_recurring: bool, priority: int = 2) -> str:
    now = datetime.now()
    deadline_context = ""
    if deadline:
        try:
            dl = datetime.fromisoformat(deadline)
            hours_left = (dl - now).total_seconds() / 3600
            if hours_left < 2:
                deadline_context = "URGENT — deadline in under 2 hours!"
            elif hours_left < 6:
                deadline_context = f"Deadline in {int(hours_left)} hours."
            elif hours_left < 24:
                deadline_context = f"Deadline today at {dl.strftime('%I:%M %p')}."
            else:
                deadline_context = f"Deadline: {dl.strftime('%A %b %d at %I:%M %p')}."
        except:
            pass

    nudge_context = "First reminder." if nudge_count == 0 else f"Nudge #{nudge_count + 1}."
    if nudge_count >= 5:
        nudge_context = f"They've been nudged {nudge_count} times and keep ignoring it. Classic procrastinator."

    priority_context = {1: "HIGH priority task.", 2: "", 3: "Low priority but still needs doing."}.get(priority, "")

    prompt = f"""Task: "{task}"
{nudge_context} {deadline_context} {priority_context}
{"Recurring reminder." if is_recurring else ""}
Remember: this user is a procrastinator. Factor that in.

Send a nudge. Be Arlo. 1-3 sentences. Vary your tone and opening each time."""

    return await _groq(prompt, temperature=0.85, max_tokens=120)


async def generate_done_response(task: str, nudge_count: int) -> str:
    prompt = f"""Task completed: "{task}" after {nudge_count} nudges.
This user is a procrastinator so celebrate extra. 1-2 sentences.
If nudge_count > 5, be mildly sarcastic about how long it took but still warm."""
    return await _groq(prompt, temperature=0.85, max_tokens=80)


# ── Morning briefing ──────────────────────────────────────────────────────────

async def generate_morning_briefing(chat_id: int) -> str | None:
    reminders = await get_all_active(chat_id)
    if not reminders:
        return "Good morning! 🌅 No tasks on your plate today. Enjoy it — but maybe add something before you procrastinate adding something. 😄"

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    task_list = "\n".join([
        f"- ID {r['id']}: {r['task']} (due: {r['deadline'] or 'no deadline'}, priority: {r.get('priority', 2)})"
        for r in reminders
    ])

    prompt = f"""Today is {now.strftime('%A, %d %B %Y')} (IST).
User's active tasks:
{task_list}

Generate a good morning briefing as Arlo. Include:
1. A warm greeting (vary it, don't always say "Good morning")
2. A prioritized list of what needs to be done today (focus on tasks due today or soon)
3. A smart suggestion — what to tackle first and why
4. A motivating closer (keep it short, you know they procrastinate)

Keep the whole message under 150 words. Use emojis sparingly. Format nicely for Telegram."""

    return await _groq(prompt, temperature=0.75, max_tokens=300)


# ── Evening check-in ──────────────────────────────────────────────────────────

async def generate_evening_checkin(chat_id: int) -> str | None:
    reminders = await get_all_active(chat_id)
    if not reminders:
        return "Evening check-in: nothing pending! Either you were super productive today or you forgot to add things. I'm guessing productive. 🌙"

    now = datetime.now()
    task_list = "\n".join([
        f"- {r['task']} (due: {r['deadline'] or 'no deadline'}, nudged {r.get('nudge_count', 0)} times)"
        for r in reminders
    ])

    prompt = f"""It's evening ({now.strftime('%I:%M %p IST')}).
User still has these incomplete tasks:
{task_list}

Generate an evening check-in as Arlo. Include:
1. A brief evening greeting
2. What's still pending (especially anything due today or tomorrow)
3. A gentle but firm nudge — remind them procrastinating tonight means stress tomorrow
4. Ask if they got anything done today

Keep it under 100 words. Warm but direct."""

    return await _groq(prompt, temperature=0.75, max_tokens=200)


# ── Weekly preview ────────────────────────────────────────────────────────────

async def generate_weekly_preview(chat_id: int) -> str | None:
    reminders = await get_all_active(chat_id)
    now = datetime.now()
    next_week = now + timedelta(days=7)

    if not reminders:
        return f"Weekly preview 📅\nClear week ahead! Perfect time to plan something. Or procrastinate planning. Your call. 😏"

    task_list = "\n".join([
        f"- {r['task']} (due: {r['deadline'] or 'no deadline'}, priority: {r.get('priority', 2)})"
        for r in reminders
    ])

    prompt = f"""It's Sunday evening. Week ahead: {now.strftime('%d %b')} to {next_week.strftime('%d %b %Y')}.
User's tasks:
{task_list}

Generate a weekly preview as Arlo. Include:
1. A Sunday evening greeting
2. What's coming up this week — organized by priority/deadline
3. One key piece of advice for the week (knowing they tend to procrastinate)
4. An encouraging closer

Keep it under 150 words. Make it feel like a friend giving you a heads-up, not a corporate report."""

    return await _groq(prompt, temperature=0.75, max_tokens=300)


# ── DB operations ─────────────────────────────────────────────────────────────

async def mark_done(chat_id: int, text: str, reminder_id: int | None = None) -> str:
    conn = get_conn()
    if reminder_id:
        row = conn.execute("SELECT id, task, nudge_count, cron_expression FROM reminders WHERE id = ? AND chat_id = ? AND done = 0", (reminder_id, chat_id)).fetchone()
    else:
        row = conn.execute("SELECT id, task, nudge_count, cron_expression FROM reminders WHERE chat_id = ? AND done = 0 ORDER BY next_nudge ASC LIMIT 1", (chat_id,)).fetchone()

    if not row:
        conn.close()
        return "No matching active reminder found. Already done? 😄"

    if row["cron_expression"]:
        every_other = conn.execute("SELECT cron_every_other FROM reminders WHERE id=?", (row["id"],)).fetchone()["cron_every_other"]
        next_dt = _next_cron_occurrence(row["cron_expression"], bool(every_other), datetime.now())
        conn.execute("UPDATE reminders SET nudge_count = 0, next_nudge = ?, last_cron_fire = ? WHERE id = ?",
                     (next_dt.strftime("%Y-%m-%dT%H:%M"), datetime.now().strftime("%Y-%m-%dT%H:%M"), row["id"]))
        conn.commit()
        conn.close()
        msg = await generate_done_response(row["task"], row["nudge_count"])
        return msg + f"\n\n_(Recurring — back {next_dt.strftime('%A %b %d at %I:%M %p')} 👀)_"
    else:
        conn.execute("UPDATE reminders SET done = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        return await generate_done_response(row["task"], row["nudge_count"])


async def snooze_reminder(chat_id: int, snooze_minutes: int = 90, original_text: str = "") -> str:
    conn = get_conn()
    row = conn.execute("SELECT id, task FROM reminders WHERE chat_id = ? AND done = 0 ORDER BY next_nudge ASC LIMIT 1", (chat_id,)).fetchone()
    if not row:
        conn.close()
        return "Nothing to snooze!"

    # If they said something vague/short, be skeptical
    if snooze_minutes <= 15:
        skeptical = True
        actual_snooze = snooze_minutes
    else:
        skeptical = False
        actual_snooze = snooze_minutes

    snooze_until = (datetime.now() + timedelta(minutes=actual_snooze)).strftime("%Y-%m-%dT%H:%M")
    conn.execute("UPDATE reminders SET next_nudge = ? WHERE id = ?", (snooze_until, row["id"]))
    conn.commit()
    conn.close()

    prompt = f"""User asked to snooze "{row['task']}" for {actual_snooze} minutes. They said: "{original_text}"
{"They said a very short time like 2-5 mins or something vague. As Arlo, be lovingly skeptical — you know them, they'll take longer. Agree to the snooze but tease them about it." if skeptical else "Acknowledge the snooze warmly."}
1-2 sentences max."""

    return await _groq(prompt, temperature=0.85, max_tokens=80)


async def get_all_active(chat_id: int) -> list:
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, task, deadline, cron_expression, cron_every_other, priority, nudge_count
        FROM reminders WHERE chat_id = ? AND done = 0 ORDER BY priority ASC, created_at ASC
    """, (chat_id,)).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if d["cron_expression"]:
            parts = d["cron_expression"].split(":")
            if parts[0] == "weekly":
                day_name = [k for k, v in DAY_MAP.items() if v == int(parts[1])][0].capitalize()
                freq = "every other" if d["cron_every_other"] else "every"
                d["deadline"] = f"{freq} {day_name} at {parts[2]}:{parts[3].zfill(2)}"
            elif parts[0] == "weekdays":
                d["deadline"] = f"every weekday at {parts[1]}:{parts[2].zfill(2)}"
        result.append(d)
    return result


async def delete_reminder(chat_id: int, reminder_id: str) -> str:
    conn = get_conn()
    conn.execute("DELETE FROM reminders WHERE id = ? AND chat_id = ?", (reminder_id, chat_id))
    conn.commit()
    conn.close()
    return f"Reminder {reminder_id} deleted. Hope you actually did it and didn't just escape. 👀"


async def get_due_reminders() -> list[dict]:
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M")
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, chat_id, task, deadline, nudge_count, interval_minutes,
               is_recurring, cron_expression, cron_every_other, last_cron_fire, priority
        FROM reminders WHERE done = 0 AND next_nudge <= ?
    """, (now_str,)).fetchall()

    results = []
    for row in rows:
        try:
            message = await generate_nudge(
                task=row["task"],
                nudge_count=row["nudge_count"],
                deadline=row["deadline"],
                is_recurring=bool(row["is_recurring"]),
                priority=row["priority"] or 2
            )
            if row["cron_expression"]:
                last_fire = datetime.fromisoformat(row["last_cron_fire"]) if row["last_cron_fire"] else None
                next_dt = _next_cron_occurrence(row["cron_expression"], bool(row["cron_every_other"]), last_fire)
                next_nudge = next_dt.strftime("%Y-%m-%dT%H:%M")
                conn.execute("UPDATE reminders SET nudge_count = nudge_count + 1, next_nudge = ?, last_cron_fire = ? WHERE id = ?",
                             (next_nudge, now_str, row["id"]))
            else:
                interval = row["interval_minutes"] or 60
                if row["deadline"]:
                    try:
                        dl = datetime.fromisoformat(row["deadline"])
                        hours_left = (dl - datetime.now()).total_seconds() / 3600
                        if hours_left < 3:
                            interval = min(interval, 30)
                        elif hours_left < 12:
                            interval = min(interval, 60)
                    except:
                        pass
                next_nudge = (datetime.now() + timedelta(minutes=interval)).strftime("%Y-%m-%dT%H:%M")
                conn.execute("UPDATE reminders SET nudge_count = nudge_count + 1, next_nudge = ? WHERE id = ?",
                             (next_nudge, row["id"]))
            results.append({"chat_id": row["chat_id"], "message": message})
        except Exception as e:
            logger.error(f"Error generating nudge for reminder {row['id']}: {e}")

    conn.commit()
    conn.close()
    return results