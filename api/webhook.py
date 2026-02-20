import re
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

from services.claude_service import estimate_calories
from services.sheets_service import (
    delete_entry,
    get_today_entries,
    log_entry,
    update_entry,
)

app = FastAPI()

DAILY_CALORIE_TARGET = 2600


# ---------------------------------------------------------------------------
# Intent classification (regex-based, no AI)
# ---------------------------------------------------------------------------

def classify_intent(text: str) -> dict:
    """
    Route natural language input to the correct action using regex patterns.

    Supported commands (case-insensitive):
      - "delete 2"                        → delete today's entry #2
      - "remove entry 2"                  → same
      - "edit 2 grilled chicken salad"    → re-estimate and update entry #2
      - "update 2: two eggs and toast"    → same
      - "summary"  / "today" / "total"    → daily summary
      - anything else                     → log calories (default)

    Entry numbers are 1-based and refer to today's entries in chronological order.
    The entry number is included in every log confirmation reply.
    """
    t = text.strip()

    # DELETE: "delete 2", "remove entry 2"
    m = re.match(r"^(?:delete|remove)(?:\s+entry)?\s+(\d+)\b", t, re.IGNORECASE)
    if m:
        return {"intent": "delete", "entry_num": int(m.group(1))}

    # EDIT: "edit 2 grilled salmon", "update 2: two eggs", "fix entry 2 oatmeal"
    m = re.match(
        r"^(?:edit|update|change|fix|correct)(?:\s+entry)?\s+(\d+)[:\s]+(.+)",
        t,
        re.IGNORECASE,
    )
    if m:
        return {
            "intent": "edit",
            "entry_num": int(m.group(1)),
            "new_description": m.group(2).strip(),
        }

    # SUMMARY: "summary", "today", "total", "stats", "show"
    if re.match(r"^(?:summary|today|total|stats|show)\b", t, re.IGNORECASE):
        return {"intent": "summary"}

    # Default: log calories
    return {"intent": "log", "description": t}


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def handle_log(description: str) -> str:
    calorie_data = estimate_calories(description)
    entry_num, daily_total = log_entry(description, calorie_data)

    items_str = "\n".join(
        f"  • {item['name']}: {item['calories']} cal"
        for item in calorie_data["items"]
    )
    remaining = DAILY_CALORIE_TARGET - daily_total

    return (
        f"Entry #{entry_num} logged: {calorie_data['total_calories']} cal\n"
        f"{items_str}\n\n"
        f"Daily total: {daily_total} / {DAILY_CALORIE_TARGET}\n"
        f"Remaining: {remaining}"
    )


def handle_edit(entry_num: int, new_description: str) -> str:
    calorie_data = estimate_calories(new_description)
    daily_total = update_entry(entry_num, new_description, calorie_data)
    remaining = DAILY_CALORIE_TARGET - daily_total

    return (
        f"Entry #{entry_num} updated: {calorie_data['total_calories']} cal\n"
        f"New daily total: {daily_total} / {DAILY_CALORIE_TARGET}\n"
        f"Remaining: {remaining}"
    )


def handle_delete(entry_num: int) -> str:
    daily_total = delete_entry(entry_num)
    remaining = DAILY_CALORIE_TARGET - daily_total

    return (
        f"Entry #{entry_num} deleted.\n"
        f"Daily total: {daily_total} / {DAILY_CALORIE_TARGET}\n"
        f"Remaining: {remaining}"
    )


def handle_summary() -> str:
    entries = get_today_entries()

    if not entries:
        return f"No entries logged today.\nTarget: {DAILY_CALORIE_TARGET} cal"

    lines = [
        f"{i}. {e['time']} - {e['description']} — {e['calories']} cal"
        for i, e in enumerate(entries, 1)
    ]
    daily_total = sum(e["calories"] for e in entries)
    remaining = DAILY_CALORIE_TARGET - daily_total

    return (
        "\n".join(lines)
        + f"\n\nTotal: {daily_total} / {DAILY_CALORIE_TARGET} cal\n"
        + f"Remaining: {remaining} cal"
    )


# ---------------------------------------------------------------------------
# Webhook entrypoint
# ---------------------------------------------------------------------------

@app.post("/api/webhook")
async def webhook(request: Request):
    """
    Single webhook endpoint for all input sources.

    Accepts:
      - JSON from Apple Shortcuts: {"food": "<user text>"}
      - Form-encoded from Twilio: Body=<user text>

    Classifies intent via regex and routes to the appropriate handler.
    Returns plain text for Shortcuts, TwiML XML for Twilio.
    """
    content_type = request.headers.get("Content-Type", "")

    if "application/json" in content_type:
        data = await request.json()
        user_text = data.get("food", "")
        source = "shortcuts"
    else:
        body = await request.body()
        parsed = parse_qs(body.decode("utf-8"))
        user_text = parsed.get("Body", [""])[0]
        source = "twilio"

    intent = classify_intent(user_text)

    try:
        if intent["intent"] == "log":
            reply = handle_log(intent["description"])
        elif intent["intent"] == "edit":
            reply = handle_edit(intent["entry_num"], intent["new_description"])
        elif intent["intent"] == "delete":
            reply = handle_delete(intent["entry_num"])
        elif intent["intent"] == "summary":
            reply = handle_summary()
        else:
            reply = "Unknown command."
    except Exception as e:
        reply = f"Error: {str(e)}"

    if source == "twilio":
        from twilio.twiml.messaging_response import MessagingResponse

        twiml = MessagingResponse()
        twiml.message(reply)
        return Response(content=str(twiml), media_type="text/xml")

    return PlainTextResponse(reply)
