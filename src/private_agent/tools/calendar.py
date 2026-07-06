"""Calendar tool via AppleScript (Calendar.app) -- no extra dependencies needed."""

import subprocess

from private_agent.tools._applescript import escape


def create_calendar_event(title: str, start_date: str, start_time: str = "09:00") -> str:
    """Create a new event on the user's default Calendar.

    Args:
        title: The event title.
        start_date: Date in MM/DD/YYYY format, e.g. "07/10/2026".
        start_time: Time in 24-hour HH:MM format, e.g. "14:30". Defaults to 09:00.
    """
    title_e = escape(title)
    script = f'''
    set theDate to date "{start_date} {start_time}"
    tell application "Calendar"
        tell calendar 1
            make new event with properties {{summary:"{title_e}", start date:theDate, end date:(theDate + 1 * hours)}}
        end tell
    end tell
    '''
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return f"Failed to create event: {result.stderr.strip()}"
    return f"Created event '{title}' on {start_date} at {start_time}."
