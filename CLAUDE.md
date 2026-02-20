# CLAUDE.md — Project Context for Claude Code

This file gives Claude Code the context needed to work on this codebase effectively.
Read it before making any changes.

## What This Is

A personal calorie tracker for a single user. The user sends a text message (via Apple
Shortcuts or Twilio SMS) to a Vercel serverless endpoint. The app uses Claude to estimate
calories, then logs the result to a Google Sheet.

**This is a personal tool, not a product.** It is intentionally simple. Do not add
abstraction layers, configuration systems, or features that aren't explicitly requested.

## Architecture

```
User input (Apple Shortcuts JSON or Twilio SMS)
    ↓
POST /api/webhook  (Vercel serverless, FastAPI ASGI app)
    ↓
classify_intent()  (regex router — NO AI involved in routing)
    ↓
┌──────────────────────────────────────┐
│ log     → Claude estimate → Sheets   │
│ edit    → Claude estimate → Sheets   │
│ delete  → Sheets                     │
│ summary → Sheets                     │
└──────────────────────────────────────┘
    ↓
Plain text reply (Shortcuts) or TwiML XML (Twilio)
```

## File Structure

```
api/webhook.py              FastAPI app — single entry point for all requests
services/claude_service.py  Claude API call for calorie estimation
services/sheets_service.py  All Google Sheets read/write operations
public/index.html           Static landing page (rarely needs changes)
requirements.txt            Python dependencies
CLAUDE.md                   This file
README.md                   User-facing project overview
```

## Key Design Decisions

**Single webhook endpoint.** All input — logging, editing, deleting, summary — goes through
`POST /api/webhook`. Intent is classified by regex, not AI. This keeps the Apple Shortcut
simple (one URL, one action).

**Regex routing, not NLP.** The user controls both the input (Shortcuts) and the endpoint,
so there's no ambiguity to resolve. Regex is faster, cheaper, and more predictable than
using Claude to classify intent. The command syntax is:
- `"<anything>"` → log calories (default)
- `"summary"` / `"today"` / `"total"` → daily summary
- `"edit 2 grilled chicken"` → update today's entry #2
- `"delete 2"` → remove today's entry #2

**Entry numbers are 1-based and today-scoped.** When a user logs food, the reply includes
`Entry #N`. That number refers to their Nth entry *today*, in chronological order. This
is what they use for edit/delete commands. It maps to a row index in the sheet at request
time (not a stored ID).

**Google Sheets as the database.** No SQL database. The sheet is the source of truth.
Column layout: Date | Time | Description | Items breakdown | Calories | Daily total.
The daily total column (F) is recalculated from scratch on every write operation.

**No authentication.** The webhook URL is the secret. This is acceptable for a personal
tool. Do not add auth unless explicitly requested.

**No local state.** Each serverless invocation is fully stateless. All state lives in
Google Sheets.

**Single user.** The daily calorie target (2,600 cal) is hardcoded. There is no user
management, login system, or per-user data isolation. Multi-user support is a v2 concern.

## Environment Variables

These must be set in Vercel (or a local `.env` for development):

| Variable            | Description                                      |
|---------------------|--------------------------------------------------|
| `ANTHROPIC_API_KEY` | Anthropic API key                                |
| `GOOGLE_CREDENTIALS`| Full JSON string of the GCP service account key  |
| `GOOGLE_SHEET_ID`   | The Google Sheet ID from its URL                 |

## Local Development

```bash
pip install -r requirements.txt
# Set env vars, then:
uvicorn api.webhook:app --reload
# Endpoint: http://localhost:8000/api/webhook
```

## Deployment

Hosted on Vercel. The file `api/webhook.py` exports a FastAPI `app` object, which Vercel
detects as an ASGI app and serves at `/api/webhook`. No `vercel.json` is required.

Push to `main` triggers a Vercel deployment automatically.

## What's Intentionally NOT Here (v2 backlog)

- **Multi-user support** — requires a real DB and auth layer; punted to v2
- **Authentication** — not needed for a personal tool with an obscure URL
- **A real database** — Google Sheets works fine for one user's data volume
- **Unit tests** — worthwhile to add, but not yet implemented
- **Timezone configurability** — hardcoded to `America/New_York`
- **Configurable calorie target** — hardcoded to 2,600; change directly in `webhook.py`

## Coding Conventions

- Keep service functions synchronous. FastAPI handles them in a thread pool when called
  from an `async` route handler. Do not introduce `asyncio` complexity.
- Error messages should be human-readable — they go directly to the user's phone.
- Do not add logging infrastructure. `print()` statements are fine for Vercel log tailing.
- Do not pin dependency versions in `requirements.txt` unless there's a known conflict.
