"""Reminders tool via AppleScript (Reminders.app) -- no extra dependencies needed."""

import subprocess
from datetime import datetime, timedelta

from private_agent.tools._applescript import escape

# The on-device model doesn't reliably follow the MM/DD/YYYY instruction in the
# docstring below -- it sometimes sends plain-language values like "today",
# which AppleScript's `date "..."` cannot parse. Normalize the common cases
# rather than trusting the model's formatting.
_RELATIVE_DATES = {
    "today": 0,
    "tomorrow": 1,
}


def _normalize_due_date(due_date: str) -> str:
    key = due_date.strip().lower()
    if key in _RELATIVE_DATES:
        target = datetime.now() + timedelta(days=_RELATIVE_DATES[key])
        return target.strftime("%m/%d/%Y")
    return due_date


def create_reminder(title: str, due_date: str = "") -> str:
    """Create a new reminder in the user's default Reminders list.

    Args:
        title: The reminder's text.
        due_date: Optional due date in MM/DD/YYYY format, e.g. "07/10/2026". Leave empty for no due date.
    """
    due_date = _normalize_due_date(due_date) if due_date else due_date
    title_e = escape(title)
    if due_date:
        script = f'''
        set dueDate to date "{due_date}"
        tell application "Reminders"
            tell default list
                make new reminder with properties {{name:"{title_e}", due date:dueDate}}
            end tell
        end tell
        '''
    else:
        script = f'''
        tell application "Reminders"
            tell default list
                make new reminder with properties {{name:"{title_e}"}}
            end tell
        end tell
        '''
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        # This account's default list has 2600+ reminders -- even a plain
        # insert (not just filtered queries/deletes) can exceed 10s at this
        # scale, confirmed by a real timeout during testing.
        timeout=90,
    )
    if result.returncode != 0:
        return f"Failed to create reminder: {result.stderr.strip()}"
    return f"Created reminder '{title}'" + (f" due {due_date}." if due_date else ".")
