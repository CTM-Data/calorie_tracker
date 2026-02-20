import json
import os

from anthropic import Anthropic


def estimate_calories(food_description: str) -> dict:
    """
    Send a food description to Claude and return structured calorie data.

    Returns a dict of the form:
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

    response_text = message.content[0].text.strip()

    # Strip markdown code fences if Claude includes them despite instructions
    if response_text.startswith("```"):
        response_text = response_text.split("\n", 1)[1]
        response_text = response_text.rsplit("```", 1)[0].strip()

    return json.loads(response_text)
