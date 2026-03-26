"""
Reminder parsing, storage, nudging, and AI personality via Groq.
"""

import os
import json
import logging
from datetime import datetime, timedelta
from groq import Groq
from db import get_conn

logger = logging.getLogger(__name__)

groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])

SYSTEM_PROMPT = """You are Arlo — a personal reminder assistant who is a mix of:
- A caring, supportive coach who genuinely wants the user to succeed
- A snarky best friend who teases them lovingly about procrastinating
- A no-nonsense assistant when needed

Your job is to help the user stop procrastinating and actually get things done.
When nudging, vary your tone — sometimes warm and encouraging, sometimes playfully sarcastic, sometimes direct.
Keep messages SHORT (1-3 sentences max). Use emojis sparingly but effectively.

Never be mean or harsh — always from a place of care."""


# ── Parse a reminder message using Groq ───────────────────────────────────────
async def parse_reminder_with_ai(user_message: str) -> dict:
    """
    Returns a dict: {task, deadline_iso, interval_minutes, is_recurring, confirmation_message}
    """
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")

    prompt = f"""Current date/time: {now_str}

User message: "{user_message}"

Parse this reminder request and return ONLY valid JSON with these fields:
{{
  "task": "short description of what they need to do",
  "deadline_iso": "YYYY-MM-DDTHH:MM or null if no deadline",
  "interval_minutes": <how often to nudge in minutes, e.g. 60 for hourly, 120 for every 2h>,
  "is_recurring": <true if they want recurring reminders like 'every day' or 'every 2 hours'>,
  "first_nudge_iso": "YYYY-MM-DDTHH:MM — when to send the FIRST nudge",
  "confirmation_message": "A short friendly confirmation that you've set the reminder (in Arlo's voice)"
}}

Rules for timing:
- "tonight" = 8pm today
- "this evening" = 6pm today  
- "this morning" = 9am today
- "tomorrow" = 9am tomorrow
- "this week" / "by Friday" = set first nudge to 30 mins from now, then nudge every 2 hours
- "every X hours" = recurring, interval = X*60 minutes
- If no time given, first nudge = 30 mins from now, interval = 60 mins
- Deadline tasks (e.g. "by Friday") should nudge more aggressively as deadline approaches

Return ONLY the JSON object, no other text."""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
        max_tokens=400
    )

    raw = response.choices[0].message.content.strip()
    # Strip markdown fences if present
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


# ── Generate a nudge message ───────────────────────────────────────────────────
async def generate_nudge(task: str, nudge_count: int, deadline: str | None, is_recurring: bool) -> str:
    now = datetime.now()
    deadline_context = ""
    urgency = ""

    if deadline:
        try:
            dl = datetime.fromisoformat(deadline)
            hours_left = (dl - now).total_seconds() / 3600
            if hours_left < 2:
                urgency = "URGENT — deadline is in under 2 hours!"
            elif hours_left < 6:
                urgency = f"Deadline in about {int(hours_left)} hours."
            elif hours_left < 24:
                urgency = f"Deadline today ({dl.strftime('%I:%M %p')})."
            else:
                urgency = f"Deadline: {dl.strftime('%A %b %d at %I:%M %p')}."
            deadline_context = urgency
        except:
            pass

    nudge_context = f"This is nudge #{nudge_count + 1}."
    if nudge_count == 0:
        nudge_context = "First reminder."
    elif nudge_count >= 5:
        nudge_context = f"You've been nudged {nudge_count} times already. They keep ignoring it."

    prompt = f"""Task: "{task}"
{nudge_context}
{deadline_context}
{"This is a recurring reminder (they asked to be reminded regularly)." if is_recurring else ""}

Send a nudge message to get them to do this task. Be Arlo. Keep it 1-3 sentences.
Don't start with "Hey" every time — vary your opening.
If they've been nudged many times, be more exasperated but still caring."""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
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
If nudge_count > 5, you can be mildly sarcastic about how long it took, but still celebrate."""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
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
    try:
        parsed = await parse_reminder_with_ai(text)
    except Exception as e:
        logger.error(f"AI parse failed: {e}")
        return "Hmm, I couldn't quite parse that. Try something like: *remind me to call doctor at 6pm*"

    conn = get_conn()
    conn.execute("""
        INSERT INTO reminders (chat_id, task, deadline, interval_minutes, next_nudge, is_recurring)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        chat_id,
        parsed.get("task", text),
        parsed.get("deadline_iso"),
        parsed.get("interval_minutes", 60),
        parsed.get("first_nudge_iso"),
        1 if parsed.get("is_recurring") else 0
    ))
    conn.commit()
    conn.close()

    return parsed.get("confirmation_message", "Got it! I'll remind you. 👍")


async def mark_done(chat_id: int, text: str) -> str:
    conn = get_conn()
    # Find the most recently nudged active reminder for this user
    row = conn.execute("""
        SELECT id, task, nudge_count FROM reminders
        WHERE chat_id = ? AND done = 0
        ORDER BY next_nudge ASC
        LIMIT 1
    """, (chat_id,)).fetchone()

    if not row:
        conn.close()
        return "No active reminders to mark done! You're either very efficient or very sneaky. 😄"

    conn.execute("UPDATE reminders SET done = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()

    return await generate_done_response(row["task"], row["nudge_count"])


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
        SELECT id, task, deadline FROM reminders
        WHERE chat_id = ? AND done = 0
        ORDER BY created_at ASC
    """, (chat_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


async def delete_reminder(chat_id: int, reminder_id: str) -> str:
    conn = get_conn()
    conn.execute("DELETE FROM reminders WHERE id = ? AND chat_id = ?", (reminder_id, chat_id))
    conn.commit()
    conn.close()
    return f"Reminder {reminder_id} deleted. Hope you actually did it and didn't just escape. 👀"


async def get_due_reminders() -> list[dict]:
    """Called by scheduler every 30 min. Returns list of {chat_id, message} to send."""
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, chat_id, task, deadline, nudge_count, interval_minutes, is_recurring
        FROM reminders
        WHERE done = 0 AND next_nudge <= ?
    """, (now,)).fetchall()

    results = []
    for row in rows:
        try:
            message = await generate_nudge(
                task=row["task"],
                nudge_count=row["nudge_count"],
                deadline=row["deadline"],
                is_recurring=bool(row["is_recurring"])
            )

            # Compute next nudge time
            interval = row["interval_minutes"] or 60

            # Escalate interval if close to deadline
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
                UPDATE reminders
                SET nudge_count = nudge_count + 1, next_nudge = ?
                WHERE id = ?
            """, (next_nudge, row["id"]))

            results.append({"chat_id": row["chat_id"], "message": message})
        except Exception as e:
            logger.error(f"Error generating nudge for reminder {row['id']}: {e}")

    conn.commit()
    conn.close()
    return results
