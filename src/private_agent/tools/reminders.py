"""Reminders tool via AppleScript (Reminders.app) -- no extra dependencies needed."""

import subprocess

from private_agent.tools._applescript import escape
from private_agent.tools._dates import normalize_date


def create_reminder(title: str, due_date: str = "") -> str:
    """Create a new reminder in the user's default Reminders list.

    Args:
        title: The reminder's text.
        due_date: Optional due date in MM/DD/YYYY format, e.g. "07/10/2026". Leave empty for no due date.
    """
    # An eval run found the model can't be trusted to compute its own
    # relative/absolute dates correctly (89% of tool calls with a due_date
    # sent something other than MM/DD/YYYY, often a specific but wrong
    # date) -- see _dates.py. Untrustworthy input becomes no due date
    # rather than a silently wrong one.
    raw_due_date = due_date
    due_date = normalize_date(due_date) or ""
    date_was_rejected = bool(raw_due_date) and not due_date
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
    if due_date:
        return f"Created reminder '{title}' due {due_date}."
    if date_was_rejected:
        # Say so explicitly -- otherwise the model may still tell the user
        # "due December 20th" from its own (wrong) context, even though no
        # due date was actually set.
        return f"Created reminder '{title}' with no due date (could not parse '{raw_due_date}' as a real date)."
    return f"Created reminder '{title}'."
