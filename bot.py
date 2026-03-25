"""
Reminder parsing, storage, nudging, and AI personality via Groq.
Supports: one-time, interval-recurring, cron (every Monday, every other Friday, etc.)
"""

import os
import json
import logging
from datetime import datetime, timedelta
from groq import Groq
from db import get_conn

logger = logging.getLogger(__name__)

groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])

MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You are Arlo — a personal reminder assistant who is a mix of:
- A caring, supportive coach who genuinely wants the user to succeed
- A snarky best friend who teases them lovingly about procrastinating
- A no-nonsense assistant when needed

Your job is to help the user stop procrastinating and actually get things done.
When nudging, vary your tone — sometimes warm and encouraging, sometimes playfully sarcastic, sometimes direct.
Keep messages SHORT (1-3 sentences max). Use emojis sparingly but effectively.

Never be mean or harsh — always from a place of care."""

DAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6
}


# ── Cron helpers ──────────────────────────────────────────────────────────────

def next_weekday(weekday: int, at_hour: int, at_minute: int, every_other: bool = False, last_fire: datetime | None = None) -> datetime:
    """Return next datetime for a given weekday + time, respecting every_other logic."""
    now = datetime.now()
    days_ahead = weekday - now.weekday()
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0:
        candidate = now.replace(hour=at_hour, minute=at_minute, second=0, microsecond=0)
        if candidate <= now:
            days_ahead = 7

    next_dt = (now + timedelta(days=days_ahead)).replace(
        hour=at_hour, minute=at_minute, second=0, microsecond=0
    )

    if every_other and last_fire:
        # If we already fired this week (within 8 days), skip one cycle
        days_since = (next_dt - last_fire).days
        if days_since < 10:
            next_dt += timedelta(weeks=1)

    return next_dt


def parse_cron_from_text(text: str) -> dict | None:
    """
    Detect day-of-week patterns and return cron info dict, or None if not a cron pattern.
    Examples: "every monday at 8:30pm", "every other friday at 9am", "every weekday at 7am"
    """
    lower = text.lower()
    every_other = "every other" in lower

    # Check for weekday name
    found_day = None
    for day_name, day_num in DAY_MAP.items():
        if day_name in lower:
            found_day = (day_name, day_num)
            break

    # Handle "every weekday"
    if "weekday" in lower and not found_day:
        # Return special marker — we'll handle Mon–Fri separately
        return {"type": "weekdays", "every_other": False, "hour": _extract_time(lower)[0], "minute": _extract_time(lower)[1]}

    if not found_day:
        return None

    hour, minute = _extract_time(lower)
    return {
        "type": "weekly",
        "day_name": found_day[0],
        "day_num": found_day[1],
        "every_other": every_other,
        "hour": hour,
        "minute": minute,
    }


def _extract_time(text: str) -> tuple[int, int]:
    """Extract hour/minute from text like 'at 8:30pm' or 'at 9am'. Defaults to 9:00."""
    import re
    # Match patterns like 8:30pm, 8pm, 20:30
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


# ── Parse a reminder message using Groq ───────────────────────────────────────
async def parse_reminder_with_ai(user_message: str) -> dict:
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")

    prompt = f"""Current date/time: {now_str}

User message: "{user_message}"

Parse this reminder request and return ONLY valid JSON with these fields:
{{
  "task": "short description of what they need to do",
  "deadline_iso": "YYYY-MM-DDTHH:MM or null if no deadline",
  "interval_minutes": <how often to nudge in minutes, e.g. 60 for hourly, 120 for every 2h. null for cron-based>,
  "is_recurring": <true if they want recurring reminders>,
  "is_cron": <true if the reminder is day-of-week based like 'every monday' or 'every other friday'>,
  "first_nudge_iso": "YYYY-MM-DDTHH:MM — when to send the FIRST nudge",
  "confirmation_message": "A short friendly confirmation in Arlo's voice"
}}

Rules for timing:
- "tonight" = 8pm today
- "this evening" = 6pm today
- "this morning" = 9am today
- "tomorrow" = 9am tomorrow
- "this week" / "by Friday" = first nudge in 30 mins, then every 2 hours
- "every X hours" = recurring interval, is_cron=false
- "every monday / every other friday / every weekday" = is_cron=true, first_nudge = next occurrence
- If no time given, first nudge = 30 mins from now, interval = 60 mins

Return ONLY the JSON object, no other text."""

    response = groq_client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
        max_tokens=400
    )

    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


# ── Generate a nudge message ───────────────────────────────────────────────────
async def generate_nudge(task: str, nudge_count: int, deadline: str | None, is_recurring: bool) -> str:
    now = datetime.now()
    deadline_context = ""

    if deadline:
        try:
            dl = datetime.fromisoformat(deadline)
            hours_left = (dl - now).total_seconds() / 3600
            if hours_left < 2:
                deadline_context = "URGENT — deadline is in under 2 hours!"
            elif hours_left < 6:
                deadline_context = f"Deadline in about {int(hours_left)} hours."
            elif hours_left < 24:
                deadline_context = f"Deadline today ({dl.strftime('%I:%M %p')})."
            else:
                deadline_context = f"Deadline: {dl.strftime('%A %b %d at %I:%M %p')}."
        except:
            pass

    nudge_context = f"This is nudge #{nudge_count + 1}."
    if nudge_count == 0:
        nudge_context = "First reminder."
    elif nudge_count >= 5:
        nudge_context = f"You've been nudged {nudge_count} times already and keep ignoring it."

    prompt = f"""Task: "{task}"
{nudge_context}
{deadline_context}
{"This is a recurring/scheduled reminder." if is_recurring else ""}

Send a nudge message. Be Arlo. Keep it 1-3 sentences.
Don't start with "Hey" every time — vary your opening.
If nudged many times, be more exasperated but still caring."""

    response = groq_client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        temperature=0.85,
        max_tokens=120
    )
    return response.choices[0].message.content.strip()


# ── Generate a done response ───────────────────────────────────────────────────
async def generate_done_response(task: str, nudge_count: int) -> str:
    prompt = f"""Task completed: "{task}"
They were nudged {nudge_count} times before finishing.

Respond as Arlo celebrating/congratulating them. 1-2 sentences.
If nudge_count > 5, be mildly sarcastic about how long it took, but still celebrate."""

    response = groq_client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        temperature=0.85,
        max_tokens=80
    )
    return response.choices[0].message.content.strip()


# ── DB operations ──────────────────────────────────────────────────────────────
async def parse_and_save_reminder(chat_id: int, text: str) -> str:
    # First check locally if this looks like a cron pattern
    cron_info = parse_cron_from_text(text)

    try:
        parsed = await parse_reminder_with_ai(text)
    except Exception as e:
        logger.error(f"AI parse failed: {e}")
        return "Hmm, I couldn't quite parse that. Try: *remind me to call doctor at 6pm* or *remind me every Monday at 9am*"

    cron_expr = None
    every_other = False
    first_nudge = parsed.get("first_nudge_iso")

    if cron_info or parsed.get("is_cron"):
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
            # Mon–Fri, find next weekday
            now = datetime.now()
            for i in range(1, 8):
                candidate = now + timedelta(days=i)
                if candidate.weekday() < 5:
                    first_nudge = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
                    break
            cron_expr = f"weekdays:{hour}:{minute}"

    conn = get_conn()
    conn.execute("""
        INSERT INTO reminders
            (chat_id, task, deadline, interval_minutes, next_nudge, is_recurring, cron_expression, cron_every_other)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        chat_id,
        parsed.get("task", text),
        parsed.get("deadline_iso"),
        parsed.get("interval_minutes", 60) if not cron_expr else None,
        first_nudge,
        1 if (parsed.get("is_recurring") or cron_expr) else 0,
        cron_expr,
        1 if every_other else 0,
    ))
    conn.commit()
    conn.close()

    return parsed.get("confirmation_message", "Got it! I'll remind you. 👍")


async def mark_done(chat_id: int, text: str) -> str:
    conn = get_conn()
    row = conn.execute("""
        SELECT id, task, nudge_count, cron_expression FROM reminders
        WHERE chat_id = ? AND done = 0
        ORDER BY next_nudge ASC
        LIMIT 1
    """, (chat_id,)).fetchone()

    if not row:
        conn.close()
        return "No active reminders to mark done! You're either very efficient or very sneaky. 😄"

    if row["cron_expression"]:
        # For cron reminders "done" means done for THIS occurrence — keep the reminder alive
        # Just reset nudge count and schedule next occurrence
        next_dt = _next_cron_occurrence(row["cron_expression"], bool(conn.execute(
            "SELECT cron_every_other FROM reminders WHERE id=?", (row["id"],)).fetchone()["cron_every_other"]),
            datetime.now())
        conn.execute("""
            UPDATE reminders SET nudge_count = 0, next_nudge = ?, last_cron_fire = ?
            WHERE id = ?
        """, (next_dt.strftime("%Y-%m-%dT%H:%M"), datetime.now().strftime("%Y-%m-%dT%H:%M"), row["id"]))
        conn.commit()
        conn.close()
        msg = await generate_done_response(row["task"], row["nudge_count"])
        return msg + f"\n\n_(Recurring reminder — I'll be back {next_dt.strftime('%A %b %d at %I:%M %p')} 👀)_"
    else:
        conn.execute("UPDATE reminders SET done = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        return await generate_done_response(row["task"], row["nudge_count"])


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


async def snooze_reminder(chat_id: int) -> str:
    conn = get_conn()
    row = conn.execute("""
        SELECT id, task FROM reminders
        WHERE chat_id = ? AND done = 0
        ORDER BY next_nudge ASC
        LIMIT 1
    """, (chat_id,)).fetchone()

    if not row:
        conn.close()
        return "Nothing to snooze!"

    snooze_until = (datetime.now() + timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M")
    conn.execute("UPDATE reminders SET next_nudge = ? WHERE id = ?", (snooze_until, row["id"]))
    conn.commit()
    conn.close()
    return f"Fine, I'll bug you about *{row['task']}* in 90 minutes. Don't think I'll forget. 😤"


async def get_all_active(chat_id: int) -> list:
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, task, deadline, cron_expression, cron_every_other FROM reminders
        WHERE chat_id = ? AND done = 0
        ORDER BY created_at ASC
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
    """Called by scheduler every minute. Returns nudges to send."""
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M")
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, chat_id, task, deadline, nudge_count, interval_minutes,
               is_recurring, cron_expression, cron_every_other, last_cron_fire
        FROM reminders
        WHERE done = 0 AND next_nudge <= ?
    """, (now_str,)).fetchall()

    results = []
    for row in rows:
        try:
            message = await generate_nudge(
                task=row["task"],
                nudge_count=row["nudge_count"],
                deadline=row["deadline"],
                is_recurring=bool(row["is_recurring"])
            )

            if row["cron_expression"]:
                # For cron reminders, nudge once then schedule next full occurrence
                last_fire = datetime.fromisoformat(row["last_cron_fire"]) if row["last_cron_fire"] else None
                next_dt = _next_cron_occurrence(
                    row["cron_expression"],
                    bool(row["cron_every_other"]),
                    last_fire
                )
                next_nudge = next_dt.strftime("%Y-%m-%dT%H:%M")
                conn.execute("""
                    UPDATE reminders
                    SET nudge_count = nudge_count + 1, next_nudge = ?, last_cron_fire = ?
                    WHERE id = ?
                """, (next_nudge, now_str, row["id"]))
            else:
                # Interval-based nudge
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
                conn.execute("""
                    UPDATE reminders SET nudge_count = nudge_count + 1, next_nudge = ?
                    WHERE id = ?
                """, (next_nudge, row["id"]))

            results.append({"chat_id": row["chat_id"], "message": message})
        except Exception as e:
            logger.error(f"Error generating nudge for reminder {row['id']}: {e}")

    conn.commit()
    conn.close()
    return results
