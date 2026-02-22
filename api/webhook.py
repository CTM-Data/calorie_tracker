import re

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from services.claude_service import estimate_calories, estimate_calories_edited
from services.sheets_service import (
    delete_entry,
    get_today_entries,
    log_entry,
    update_entry,
)

app = FastAPI()

# The user's daily calorie target. Change this directly if your target changes.
DAILY_CALORIE_TARGET = 2000


# ---------------------------------------------------------------------------
# Intent classification (regex-based — no AI involved in routing)
#
# All input comes as plain text from Apple Shortcuts. We classify the intent
# purely by looking at how the message starts. The default action (no matching
# prefix) is to log calories, since that's what the user does most often.
#
# Why regex and not Claude? We control both ends (the Shortcut and the webhook),
# so there's no real ambiguity. Regex is instant, free, and predictable.
# ---------------------------------------------------------------------------

# Mapping for spelled-out numbers, e.g. from voice input ("delete three")
_WORD_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}

def _parse_num(s: str) -> int:
    """Convert a digit string or spelled-out number to int."""
    return _WORD_TO_NUM.get(s.lower(), None) or int(s)

_NUM_PATTERN = r"(\d+|" + "|".join(_WORD_TO_NUM) + r")"


def classify_intent(text: str) -> dict:
    """
    Inspect the incoming text and return a dict describing what action to take.

    Returns one of:
        {"intent": "log",     "description": str}
        {"intent": "edit",    "entry_num": int, "edit_instruction": str}
        {"intent": "delete",  "entry_num": int}
        {"intent": "summary"}

    Entry numbers are 1-based and refer to today's entries in chronological order.
    The entry number is shown in every log confirmation reply so the user knows
    what number to use for edit/delete commands.
    """
    t = text.strip()

    # --- Delete ---
    # Matches: "delete 2", "remove 2", "delete entry 2", "remove entry 2"
    # Also matches spelled-out numbers: "delete three", "remove entry five"
    m = re.match(r"^(?:delete|remove)(?:\s+entry)?\s+" + _NUM_PATTERN + r"\b", t, re.IGNORECASE)
    if m:
        return {"intent": "delete", "entry_num": _parse_num(m.group(1))}

    # --- Edit ---
    # Matches: "edit 2 sorry it was one egg"
    #          "update 2: I think you overestimated the peanut butter"
    #          "fix entry 2 it was salmon not chicken"
    # Also matches spelled-out numbers: "edit three it was salmon not chicken"
    #
    # The entry number is captured as group 1.
    # Everything after the number (and optional colon/space) is the edit
    # instruction — it can be a full replacement description OR a natural
    # language correction. Claude figures out the difference (see handle_edit).
    m = re.match(
        r"^(?:edit|update|change|fix|correct)(?:\s+entry)?\s+" + _NUM_PATTERN + r"[:\s]+(.+)",
        t,
        re.IGNORECASE,
    )
    if m:
        return {
            "intent": "edit",
            "entry_num": _parse_num(m.group(1)),
            "edit_instruction": m.group(2).strip(),  # raw instruction, not a full description
        }

    # --- Summary ---
    # Matches: "summary", "today", "total", "stats", "show"
    # \b ensures "showing" or "totally" don't accidentally match.
    if re.match(r"^(?:summary|today|total|stats|show)\b", t, re.IGNORECASE):
        return {"intent": "summary"}

    # --- Default: log calories ---
    # Anything that didn't match a command is treated as a food description.
    return {"intent": "log", "description": t}


# ---------------------------------------------------------------------------
# Action handlers
#
# Each handler takes the parsed intent fields, calls the relevant services,
# and returns a plain-text string to send back to the user.
# ---------------------------------------------------------------------------

def handle_log(description: str) -> str:
    """Estimate calories for a new food entry and append it to the sheet."""

    # Ask Claude to estimate calories and break down the items
    calorie_data = estimate_calories(description)

    # Write the row to Sheets; get back the entry's position today (for future edits)
    entry_num, daily_total = log_entry(description, calorie_data)

    # Build the itemized breakdown for the reply
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


def handle_edit(entry_num: int, edit_instruction: str) -> str:
    """
    Edit an existing entry using a natural language instruction.

    The instruction can be a full replacement ("two eggs, toast, OJ") or a
    partial correction ("sorry, it was one egg not two" / "I think you
    overestimated the Kirkland peanut butter"). Claude sees the original entry
    and the instruction together, so it can handle both cases.
    """

    # Fetch today's entries so we can pull the original description.
    # We need this because partial corrections like "it was one egg not two"
    # only make sense in the context of what was originally logged.
    entries = get_today_entries()

    # Validate the entry number before calling Claude, to avoid a wasted API call.
    if entry_num < 1 or entry_num > len(entries):
        count = len(entries)
        noun = "entry" if count == 1 else "entries"
        return f"Entry #{entry_num} not found. You have {count} {noun} today."

    original_description = entries[entry_num - 1]["description"]

    # Pass both the original entry and the correction to Claude.
    # Claude returns updated calorie data plus a clean corrected_description
    # (the canonical text to store in the sheet going forward).
    calorie_data = estimate_calories_edited(original_description, edit_instruction)

    # Pull corrected_description out of the dict before passing to update_entry
    # (which only expects "items" and "total_calories")
    corrected_description = calorie_data.pop("corrected_description")

    # Overwrite the row in Sheets and recalculate all of today's running totals
    daily_total = update_entry(entry_num, corrected_description, calorie_data)
    remaining = DAILY_CALORIE_TARGET - daily_total

    return (
        f"Entry #{entry_num} updated: {calorie_data['total_calories']} cal\n"
        f"New daily total: {daily_total} / {DAILY_CALORIE_TARGET}\n"
        f"Remaining: {remaining}"
    )


def handle_delete(entry_num: int) -> str:
    """Remove an entry from the sheet and recalculate today's running totals."""

    # delete_entry raises ValueError if entry_num is out of range
    daily_total = delete_entry(entry_num)
    remaining = DAILY_CALORIE_TARGET - daily_total

    return (
        f"Entry #{entry_num} deleted.\n"
        f"Daily total: {daily_total} / {DAILY_CALORIE_TARGET}\n"
        f"Remaining: {remaining}"
    )


def handle_summary() -> str:
    """Return a numbered list of today's entries with a running total."""

    entries = get_today_entries()

    if not entries:
        return f"No entries logged today.\nTarget: {DAILY_CALORIE_TARGET} cal"

    # Build one line per entry: "1. 08:30 AM - oatmeal — 380 cal"
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
    Single POST endpoint for all input from Apple Shortcuts.

    The Shortcut sends JSON: {"food": "<whatever the user typed>"}
    We classify the intent, run the appropriate handler, and return plain text
    which the Shortcut displays as a notification or reads aloud.
    """

    # Parse the JSON body from the Shortcut
    data = await request.json()
    user_text = data.get("food", "")

    # Determine what the user wants to do (log / edit / delete / summary)
    intent = classify_intent(user_text)

    # Route to the right handler; catch any errors and surface them as readable text
    try:
        if intent["intent"] == "log":
            reply = handle_log(intent["description"])
        elif intent["intent"] == "edit":
            reply = handle_edit(intent["entry_num"], intent["edit_instruction"])
        elif intent["intent"] == "delete":
            reply = handle_delete(intent["entry_num"])
        elif intent["intent"] == "summary":
            reply = handle_summary()
        else:
            reply = "Unknown command."
    except Exception as e:
        # Errors go back to the user's phone as plain text, so keep them readable
        reply = f"Error: {str(e)}"

    return PlainTextResponse(reply)
