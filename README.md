# calorie_tracker

Personal calorie tracker: send a text → AI estimates calories → logs to Google Sheets.

## How It Works

1. **Trigger**: Open an Apple Shortcut (or send an SMS via Twilio)
2. **Input**: Type what you ate in plain English
3. **Processing**: Vercel serverless function calls Claude to estimate calories
4. **Storage**: Entry is logged to a Google Sheet with a running daily total
5. **Reply**: Get back an itemized breakdown and your remaining calories for the day

## Commands

All commands go through a single webhook. Intent is classified by regex — no AI routing.

| Input | Action |
|-------|--------|
| `two eggs and toast` | Log calories (anything not matching a command) |
| `summary` / `today` / `total` | Show today's entries and totals |
| `edit 2 grilled chicken salad` | Re-estimate and update today's entry #2 |
| `update 2: oatmeal with berries` | Same as edit |
| `delete 2` | Remove today's entry #2 |

Entry numbers are shown in every log confirmation reply (`Entry #2 logged: 520 cal`).

## Example Replies

**Logging:**
```
Entry #1 logged: 380 cal
  • Oatmeal: 150 cal
  • Banana: 90 cal
  • Coffee with milk: 40 cal
  • Blueberries: 100 cal

Daily total: 380 / 2600
Remaining: 2220
```

**Summary:**
```
1. 08:30 AM - two eggs and toast — 380 cal
2. 12:15 PM - grilled chicken salad — 520 cal

Total: 900 / 2600 cal
Remaining: 1700 cal
```

## Stack

- **Runtime**: [Vercel](https://vercel.com) serverless (Python, ASGI)
- **Framework**: [FastAPI](https://fastapi.tiangolo.com)
- **AI**: [Claude](https://anthropic.com) (`claude-sonnet-4`) for calorie estimation
- **Storage**: Google Sheets via [gspread](https://gspread.readthedocs.io)
- **SMS (optional)**: [Twilio](https://twilio.com)

## Setup

### Environment Variables

Set these in Vercel (Project Settings → Environment Variables):

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `GOOGLE_CREDENTIALS` | Full JSON string of a GCP service account key |
| `GOOGLE_SHEET_ID` | Sheet ID from the Google Sheets URL |

### Google Sheets

The sheet must have a header row. Column layout:

| A | B | C | D | E | F |
|---|---|---|---|---|---|
| Date | Time | Description | Items breakdown | Calories | Daily total |

### Apple Shortcuts

Create a shortcut that:
1. Asks for text input
2. Posts JSON to `https://<your-vercel-domain>/api/webhook`
   ```json
   {"food": "<input>"}
   ```
3. Shows the response text

### Local Development

```bash
pip install -r requirements.txt
# Set ANTHROPIC_API_KEY, GOOGLE_CREDENTIALS, GOOGLE_SHEET_ID in your environment
uvicorn api.webhook:app --reload
# POST to http://localhost:8000/api/webhook
```

## Configuration

- **Daily calorie target**: hardcoded to `2600` in `api/webhook.py`
- **Timezone**: hardcoded to `America/New_York` in `services/sheets_service.py`
- **Claude model**: set in `services/claude_service.py`

See [CLAUDE.md](CLAUDE.md) for architecture details and development guidelines.
