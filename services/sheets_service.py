import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
TZ = ZoneInfo("America/New_York")

# Sheet column indices (1-based for gspread update_cell)
COL_DATE = 1        # A - YYYY-MM-DD
COL_TIME = 2        # B - HH:MM AM/PM
COL_DESC = 3        # C - food description
COL_ITEMS = 4       # D - "item (cal), item (cal)" breakdown
COL_CALS = 5        # E - calories for this entry
COL_DAILY = 6       # F - running daily total (recalculated on every write)


def _get_sheet():
    creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_json, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(os.environ["GOOGLE_SHEET_ID"]).sheet1


def _today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _now_time_str() -> str:
    return datetime.now(TZ).strftime("%I:%M %p")


def _get_today_row_indices(sheet, today: str) -> list[int]:
    """
    Return the 1-based sheet row indices for today's entries (row 1 is the header).
    Indices are returned in chronological order (as they appear in the sheet).
    """
    all_rows = sheet.get_all_values()
    return [
        i + 2  # +2 because all_rows[0] is header = sheet row 1, so all_rows[1] = row 2
        for i, row in enumerate(all_rows[1:])
        if row and row[0] == today
    ]


def _recalculate_daily_totals(sheet, today: str) -> int:
    """
    Recompute and write running daily totals for all of today's entries.
    Returns the final daily total.
    """
    row_indices = _get_today_row_indices(sheet, today)
    running = 0
    for row_idx in row_indices:
        row = sheet.row_values(row_idx)
        try:
            entry_cals = int(row[COL_CALS - 1])
        except (ValueError, IndexError):
            entry_cals = 0
        running += entry_cals
        sheet.update_cell(row_idx, COL_DAILY, running)
    return running


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_entry(description: str, calorie_data: dict) -> tuple[int, int]:
    """
    Append a new calorie entry.

    Returns:
        (entry_num, daily_total)
        entry_num: 1-based position among today's entries (used for edit/delete references)
        daily_total: running total for the day after this entry
    """
    sheet = _get_sheet()
    today = _today_str()

    items_breakdown = ", ".join(
        f"{item['name']} ({item['calories']})"
        for item in calorie_data["items"]
    )

    existing_indices = _get_today_row_indices(sheet, today)
    running_total = 0
    for idx in existing_indices:
        row = sheet.row_values(idx)
        try:
            running_total += int(row[COL_CALS - 1])
        except (ValueError, IndexError):
            pass

    daily_total = running_total + calorie_data["total_calories"]

    sheet.append_row([
        today,
        _now_time_str(),
        description,
        items_breakdown,
        calorie_data["total_calories"],
        daily_total,
    ])

    entry_num = len(existing_indices) + 1
    return entry_num, daily_total


def get_today_entries() -> list[dict]:
    """
    Return all of today's entries as a list of dicts, in chronological order.

    Each dict has: time, description, items, calories
    """
    sheet = _get_sheet()
    today = _today_str()
    row_indices = _get_today_row_indices(sheet, today)

    entries = []
    for idx in row_indices:
        row = sheet.row_values(idx)
        entries.append({
            "time": row[COL_TIME - 1] if len(row) >= COL_TIME else "",
            "description": row[COL_DESC - 1] if len(row) >= COL_DESC else "",
            "items": row[COL_ITEMS - 1] if len(row) >= COL_ITEMS else "",
            "calories": int(row[COL_CALS - 1]) if len(row) >= COL_CALS and row[COL_CALS - 1] else 0,
        })
    return entries


def update_entry(entry_num: int, new_description: str, calorie_data: dict) -> int:
    """
    Replace today's entry #entry_num with new description and calorie data.
    Recalculates daily totals for all of today's entries.

    Returns the new daily total.
    Raises ValueError if entry_num is out of range.
    """
    sheet = _get_sheet()
    today = _today_str()
    row_indices = _get_today_row_indices(sheet, today)

    if entry_num < 1 or entry_num > len(row_indices):
        raise ValueError(
            f"Entry #{entry_num} not found. You have {len(row_indices)} "
            f"entr{'y' if len(row_indices) == 1 else 'ies'} today."
        )

    target_row = row_indices[entry_num - 1]
    items_breakdown = ", ".join(
        f"{item['name']} ({item['calories']})"
        for item in calorie_data["items"]
    )

    sheet.update_cell(target_row, COL_DESC, new_description)
    sheet.update_cell(target_row, COL_ITEMS, items_breakdown)
    sheet.update_cell(target_row, COL_CALS, calorie_data["total_calories"])

    return _recalculate_daily_totals(sheet, today)


def delete_entry(entry_num: int) -> int:
    """
    Delete today's entry #entry_num and recalculate daily totals.

    Returns the new daily total (0 if no entries remain).
    Raises ValueError if entry_num is out of range.
    """
    sheet = _get_sheet()
    today = _today_str()
    row_indices = _get_today_row_indices(sheet, today)

    if entry_num < 1 or entry_num > len(row_indices):
        raise ValueError(
            f"Entry #{entry_num} not found. You have {len(row_indices)} "
            f"entr{'y' if len(row_indices) == 1 else 'ies'} today."
        )

    target_row = row_indices[entry_num - 1]
    sheet.delete_rows(target_row)

    # _recalculate_daily_totals re-fetches row indices, so deletion is already reflected
    return _recalculate_daily_totals(sheet, today)
