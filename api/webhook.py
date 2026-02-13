from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs
from datetime import datetime
import json
import os
from zoneinfo import ZoneInfo

from anthropic import Anthropic
import gspread
from google.oauth2.service_account import Credentials
from twilio.twiml.messaging_response import MessagingResponse


# --- Configuration ---
DAILY_CALORIE_TARGET = 2600
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_sheets_client():
    """Connect to Google Sheets using service account credentials."""
    # Vercel stores env vars as strings, so we parse the JSON credentials
    creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_json, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client


def estimate_calories(food_description):
    """Send the food description to Claude and get back a calorie estimate."""
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": food_description,
            }
        ],
        system="""You are a calorie estimation assistant. The user will describe what they ate. 
        
            Respond ONLY with valid JSON in this exact format, no other text:
            {
                "items": [
                    {"name": "item name", "calories": 200},
                    {"name": "item name", "calories": 150}
                ],
                "total_calories": 350
            }

            Be reasonable with estimates. Use typical serving sizes when not specified. 
            Round calories to the nearest 5.""",
    )

    # Parse Claude's JSON response
    response_text = message.content[0].text
    return json.loads(response_text)


def log_to_sheets(description, calorie_data):
    """Write the calorie entry to Google Sheets and return the daily total."""
    client = get_sheets_client()
    sheet = client.open_by_key(os.environ["GOOGLE_SHEET_ID"]).sheet1

    now = datetime.now(ZoneInfo("America/New_York"))
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%I:%M %p")

    # Build a readable items breakdown string
    items_breakdown = ", ".join(
        f"{item['name']} ({item['calories']})" for item in calorie_data["items"]
    )

    # Calculate daily total by reading today's existing entries
    all_rows = sheet.get_all_values()
    daily_total = calorie_data["total_calories"]
    for row in all_rows[1:]:  # Skip header row
        if row[0] == date_str:  # Same date
            try:
                daily_total += int(row[4])  # Column E = Calories
            except (ValueError, IndexError):
                pass

    # Append the new row
    sheet.append_row(
        [date_str, time_str, description, items_breakdown, calorie_data["total_calories"], daily_total]
    )

    return daily_total


def build_reply(calorie_data, daily_total):
    """Build the SMS reply text."""
    items_str = "\n".join(
        f"  • {item['name']}: {item['calories']} cal" for item in calorie_data["items"]
    )
    remaining = DAILY_CALORIE_TARGET - daily_total

    reply = f"Logged {calorie_data['total_calories']} cal\n"
    reply += f"{items_str}\n\n"
    reply += f"Daily total: {daily_total} / {DAILY_CALORIE_TARGET}\n"
    reply += f"Remaining: {remaining}"

    return reply


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Read the incoming request body from Twilio
        content_length = int(self.headers["Content-Length"])
        body = self.rfile.read(content_length).decode("utf-8")

        # Parse the form data to extract the text message
        # Check if this is from Apple Shortcuts (JSON) or Twilio (form data)
        content_type = self.headers.get("Content-Type", "")

        if "application/json" in content_type:
            # From Apple Shortcuts
            data = json.loads(body)
            food_description = data.get("food", "")
            source = "shortcuts"
        else:
            # From Twilio
            parsed = parse_qs(body)
            food_description = parsed.get("Body", [""])[0]
            source = "twilio"

        # try:
        #     # Send to Claude for calorie estimation
        #     calorie_data = estimate_calories(food_description)

        #     # Log to Google Sheets and get daily total
        #     daily_total = log_to_sheets(food_description, calorie_data)

        #     # Build the reply message
        #     reply_text = build_reply(calorie_data, daily_total)

        # except Exception as e:
        #     reply_text = f"Error logging calories: {str(e)}"

        try:
            calorie_data = estimate_calories(food_description)
        except Exception as e:
            reply_text = f"Error in estimate_calories: {str(e)}"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(reply_text.encode("utf-8"))
            return

        try:
            daily_total = log_to_sheets(food_description, calorie_data)
        except Exception as e:
            reply_text = f"Error in log_to_sheets: {str(e)}"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(reply_text.encode("utf-8"))
            return

        try:
            reply_text = build_reply(calorie_data, daily_total)
        except Exception as e:
            reply_text = f"Error in build_reply: {str(e)}"

        # Send TwiML response back to Twilio
        if source == "twilio":
            twiml = MessagingResponse()
            twiml.message(reply_text)
    
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.end_headers()
            self.wfile.write(str(twiml).encode("utf-8"))
        else: 
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(reply_text.encode("utf-8"))
