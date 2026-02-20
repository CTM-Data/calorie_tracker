import json
import os

from anthropic import Anthropic


def _parse_response(response_text: str) -> dict:
    """
    Parse Claude's text response into a Python dict.

    Claude is instructed to return raw JSON, but sometimes wraps it in markdown
    code fences (```json ... ```) despite the instructions. This strips those
    fences before parsing so we don't blow up on valid-but-wrapped responses.
    """
    text = response_text.strip()

    # Strip opening fence line (e.g. "```json" or "```")
    if text.startswith("```"):
        text = text.split("\n", 1)[1]       # drop the first line
        text = text.rsplit("```", 1)[0]     # drop everything after the closing fence
        text = text.strip()

    return json.loads(text)


def estimate_calories(food_description: str) -> dict:
    """
    Send a plain-text food description to Claude and get back a calorie estimate.

    This is called for new log entries. The user's raw text is sent as-is;
    Claude interprets serving sizes and ambiguous language on its own.

    Returns:
        {
            "items": [{"name": str, "calories": int}, ...],
            "total_calories": int,
        }
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": food_description}],
        system="""You are a calorie estimation assistant. The user will describe what they ate.

Respond ONLY with valid JSON in this exact format, no other text:
{
    "items": [
        {"name": "item name", "calories": 200},
        {"name": "item name", "calories": 150}
    ],
    "total_calories": 350
}

Do NOT wrap your response in markdown code fences or backticks. Return raw JSON only.
Be reasonable with estimates. Use typical serving sizes when not specified.
Round calories to the nearest 5.""",
    )

    return _parse_response(message.content[0].text)


def estimate_calories_edited(original_description: str, edit_instruction: str) -> dict:
    """
    Re-estimate calories for an entry the user wants to correct.

    Unlike estimate_calories(), this function receives TWO pieces of information:
      1. The original entry as it was logged (e.g. "two eggs fried in olive oil, one apple")
      2. The user's edit instruction — which can be either:
         a. A full replacement: "one egg fried, one apple, black coffee"
         b. A partial correction: "sorry it was one egg not two"
                                  "I think you overestimated the Kirkland peanut butter"

    Claude sees both and figures out what changed. It returns the updated calorie
    breakdown plus a corrected_description — a clean, canonical description of
    what was actually eaten, which gets stored in the sheet going forward.

    Returns:
        {
            "corrected_description": str,          ← stored as the new description in Sheets
            "items": [{"name": str, "calories": int}, ...],
            "total_calories": int,
        }
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # The user message gives Claude both the original entry and the correction.
    # Formatting it this way makes the relationship between them unambiguous.
    user_message = (
        f"Original entry: {original_description}\n"
        f"Correction: {edit_instruction}"
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": user_message}],
        system="""You are a calorie estimation assistant helping a user correct a food log entry.

You will receive:
  - The original food entry that was logged
  - A correction or edit instruction from the user

The correction might be a full replacement ("one egg, toast, OJ") or a partial note
("sorry it was one egg not two" / "I think you overestimated the peanut butter").
Apply the correction and return updated calorie estimates.

Respond ONLY with valid JSON in this exact format, no other text:
{
    "corrected_description": "full corrected description of what was eaten",
    "items": [
        {"name": "item name", "calories": 200},
        {"name": "item name", "calories": 150}
    ],
    "total_calories": 350
}

Rules:
- corrected_description should be a clean, complete description of what was actually eaten
- If calories were disputed ("you overestimated X"), use better judgment for that item
- Round calories to the nearest 5
- Do NOT wrap your response in markdown code fences. Return raw JSON only.""",
    )

    return _parse_response(message.content[0].text)
