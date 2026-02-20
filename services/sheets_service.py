import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
TZ = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Column map
#
# The sheet has a header row (row 1) and one data row per log entry.
# gspread's update_cell() uses 1-based column indices, so these constants
# match the visual column letters in Google Sheets.
# ---------------------------------------------------------------------------
COL_DATE = 1    # A — YYYY-MM-DD
COL_TIME = 2    # B — HH:MM AM/PM (Eastern)
COL_DESC = 3    # C — what the user typed (or corrected_description after an edit)
COL_ITEMS = 4   # D — "item (cal), item (cal)" breakdown string
COL_CALS = 5    # E — calories for this single entry
COL_DAILY = 6   # F — running daily total; recalculated on every write


# ---------------------------------------------------------------------------
# Internal helpers (prefixed with _ to signal they're not part of the public API)
# ---------------------------------------------------------------------------

def _get_sheet():
    """
    Authenticate with the Google Sheets API and return the first sheet object.

    Credentials come from the GOOGLE_CREDENTIALS env var, which should be the
    full JSON of a GCP service account key (set in Vercel's project settings).
    """
    creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_json, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(os.environ["GOOGLE_SHEET_ID"]).sheet1


def _today_str() -> str:
    """Return today's date as YYYY-MM-DD in Eastern time."""
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _now_time_str() -> str:
    """Return the current time as HH:MM AM/PM in Eastern time."""
    return datetime.now(TZ).strftime("%I:%M %p")


def _get_today_row_indices(sheet, today: str) -> list[int]:
    """
    Return the 1-based sheet row indices for all of today's entries.

    Row 1 is always the header, so data starts at row 2. The returned indices
    are in chronological order (top to bottom in the sheet), which is the same
    order the user sees when they ask for a summary and how edit/delete entry
    numbers are assigned.

    Example: if today has 3 entries at rows 5, 6, 7, this returns [5, 6, 7].
    Entry #1 → row 5, Entry #2 → row 6, Entry #3 → row 7.
    """
    all_rows = sheet.get_all_values()  # returns every row as a list of strings

    # all_rows[0] is the header (sheet row 1).
    # all_rows[1] is the first data row (sheet row 2), hence the +2 offset.
    return [
        i + 2
        for i, row in enumerate(all_rows[1:])
        if row and row[0] == today  # column A holds the date
    ]


def _recalculate_daily_totals(sheet, today: str) -> int:
    """
    Walk through today's entries in order and write a running total to column F.

    This is called after every edit or delete to keep the daily total column
    accurate. (After a plain log we already know the total, so we write it
    directly in log_entry instead of calling this.)

    Returns the final daily total after all entries are summed.
    """
    row_indices = _get_today_row_indices(sheet, today)
    running = 0
    for row_idx in row_indices:
        row = sheet.row_values(row_idx)
        try:
            # Column E holds the per-entry calorie count
            entry_cals = int(row[COL_CALS - 1])
        except (ValueError, IndexError):
            # Defensive: skip rows with missing or non-numeric calorie values
            entry_cals = 0
        running += entry_cals
        # Write the running total to column F for this row
        sheet.update_cell(row_idx, COL_DAILY, running)
    return running


# ---------------------------------------------------------------------------
# Public API — called from api/webhook.py
# ---------------------------------------------------------------------------

def log_entry(description: str, calorie_data: dict) -> tuple[int, int]:
    """
    Append a new calorie entry to the sheet.

    Args:
        description:  The user's raw food description (stored verbatim in col C).
        calorie_data: The dict returned by estimate_calories(), containing
                      "items" (list of name/calorie pairs) and "total_calories".

    Returns:
        (entry_num, daily_total)
        - entry_num:   1-based position among today's entries.
                       Shown in the reply so the user knows what number to use
                       when they later say "edit 2 ..." or "delete 2".
        - daily_total: Running calorie total for today after this entry.
    """
    sheet = _get_sheet()
    today = _today_str()

    # Build a human-readable items string for column D: "Egg (90), Toast (120)"
    items_breakdown = ", ".join(
        f"{item['name']} ({item['calories']})"
        for item in calorie_data["items"]
    )

    # Sum up calories from existing entries today to get the new running total
    existing_indices = _get_today_row_indices(sheet, today)
    running_total = 0
    for idx in existing_indices:
        row = sheet.row_values(idx)
        try:
            running_total += int(row[COL_CALS - 1])
        except (ValueError, IndexError):
            pass  # skip malformed rows

    daily_total = running_total + calorie_data["total_calories"]

    # Append the new row at the bottom of the sheet
    sheet.append_row([
        today,
        _now_time_str(),
        description,
        items_breakdown,
        calorie_data["total_calories"],
        daily_total,
    ])

    # entry_num is how many entries existed before this one, plus 1
    entry_num = len(existing_indices) + 1
    return entry_num, daily_total


def get_today_entries() -> list[dict]:
    """
    Return all of today's logged entries as a list, in chronological order.

    Called by handle_summary() to build the reply, and by handle_edit() to
    retrieve the original description before sending it to Claude.

    Each dict in the list has:
        time        — "08:30 AM"
        description — "two eggs fried in olive oil, one apple"
        items       — "Egg (90), Olive oil (40), Apple (80)"  (col D string)
        calories    — 210  (int, col E)
    """
    sheet = _get_sheet()
    today = _today_str()
    row_indices = _get_today_row_indices(sheet, today)

    entries = []
    for idx in row_indices:
        row = sheet.row_values(idx)
        # Use .get-style access with len() guards so missing columns don't crash
        entries.append({
            "time":        row[COL_TIME  - 1] if len(row) >= COL_TIME  else "",
            "description": row[COL_DESC  - 1] if len(row) >= COL_DESC  else "",
            "items":       row[COL_ITEMS - 1] if len(row) >= COL_ITEMS else "",
            "calories":    int(row[COL_CALS - 1])
                           if len(row) >= COL_CALS and row[COL_CALS - 1] else 0,
        })
    return entries


def update_entry(entry_num: int, new_description: str, calorie_data: dict) -> int:
    """
    Overwrite an existing entry's description and calorie data, then
    recalculate daily totals for all of today's entries.

    Args:
        entry_num:       1-based index of today's entry to update.
        new_description: The corrected description (from Claude's
                         corrected_description field), stored in col C.
        calorie_data:    Updated {"items": [...], "total_calories": int} from Claude.

    Returns the new daily total after recalculation.
    Raises ValueError if entry_num is out of range.
    """
    sheet = _get_sheet()
    today = _today_str()
    row_indices = _get_today_row_indices(sheet, today)

    # Validate before touching the sheet
    if entry_num < 1 or entry_num > len(row_indices):
        count = len(row_indices)
        noun = "entry" if count == 1 else "entries"
        raise ValueError(f"Entry #{entry_num} not found. You have {count} {noun} today.")

    # Convert 1-based entry_num to the actual sheet row index
    target_row = row_indices[entry_num - 1]

    # Rebuild the items breakdown string for column D
    items_breakdown = ", ".join(
        f"{item['name']} ({item['calories']})"
        for item in calorie_data["items"]
    )

    # Update columns C, D, E in place — time (col B) stays the same
    sheet.update_cell(target_row, COL_DESC,  new_description)
    sheet.update_cell(target_row, COL_ITEMS, items_breakdown)
    sheet.update_cell(target_row, COL_CALS,  calorie_data["total_calories"])

    # Recalculate running totals for all of today's rows so col F stays accurate
    return _recalculate_daily_totals(sheet, today)


def delete_entry(entry_num: int) -> int:
    """
    Remove an entry row from the sheet and recalculate daily totals.

    After deletion, _recalculate_daily_totals re-fetches today's row indices
    from the sheet, so the deleted row is already gone from its count.

    Returns the new daily total (0 if no entries remain today).
    Raises ValueError if entry_num is out of range.
    """
    sheet = _get_sheet()
    today = _today_str()
    row_indices = _get_today_row_indices(sheet, today)

    # Validate before touching the sheet
    if entry_num < 1 or entry_num > len(row_indices):
        count = len(row_indices)
        noun = "entry" if count == 1 else "entries"
        raise ValueError(f"Entry #{entry_num} not found. You have {count} {noun} today.")

    # Delete the target row; all rows below it shift up automatically in Sheets
    target_row = row_indices[entry_num - 1]
    sheet.delete_rows(target_row)

    # Recalculate totals for whatever entries remain today
    return _recalculate_daily_totals(sheet, today)
