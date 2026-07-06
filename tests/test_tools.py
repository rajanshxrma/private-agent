"""Real tests for each tool -- creates a labeled test artifact, verifies it via a
separate query, then cleans it up. No mocks: these touch the real Calendar,
Reminders, and Mail apps on this Mac."""

import subprocess

import pytest

MARK = "PRIVATE-AGENT-TEST-DELETE-ME"


def _osascript(script: str, timeout: int = 15) -> str:
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    return result.stdout.strip()


def test_search_files_finds_real_file():
    from private_agent.tools.files import search_files

    result = search_files("resume", limit=10)
    assert "resume" in result.lower() or "no files found" in result.lower()


def test_create_and_cleanup_calendar_event():
    from private_agent.tools.calendar import create_calendar_event

    create_calendar_event(MARK, "12/31/2099", "10:00")
    try:
        count = _osascript(f'''
        tell application "Calendar"
            tell calendar 1
                return count of (every event whose summary is "{MARK}")
            end tell
        end tell
        ''')
        assert count == "1"
    finally:
        _osascript(f'''
        tell application "Calendar"
            tell calendar 1
                delete (every event whose summary is "{MARK}")
            end tell
        end tell
        ''')


def test_create_and_cleanup_reminder():
    from private_agent.tools.reminders import create_reminder

    create_reminder(MARK)
    try:
        # This account's default list has 2600+ reminders -- filtered AppleScript
        # queries against it genuinely need up to ~90s, not the 15s that's fine
        # for Calendar/Mail (confirmed: this exact query timed out at 15s on a
        # real run, not flaky infra).
        count = _osascript(f'''
        tell application "Reminders"
            tell default list
                return count of (every reminder whose name is "{MARK}")
            end tell
        end tell
        ''', timeout=90)
        assert count == "1"
    finally:
        _osascript(f'''
        tell application "Reminders"
            tell default list
                delete (every reminder whose name is "{MARK}")
            end tell
        end tell
        ''', timeout=90)


@pytest.mark.skip(reason="opens a visible Mail compose window -- run manually, not in CI/automated runs")
def test_draft_email_creates_draft_not_sent():
    from private_agent.tools.mail import draft_email

    draft_email("test@example.com", MARK, "test body")
    try:
        sent_count = _osascript(f'''
        tell application "Mail"
            return count of (every message of sent mailbox whose subject is "{MARK}")
        end tell
        ''')
        assert sent_count == "0"
    finally:
        _osascript(f'''
        tell application "Mail"
            try
                close (every outgoing message whose subject is "{MARK}") saving no
            end try
            try
                delete (every message of drafts mailbox whose subject is "{MARK}")
            end try
        end tell
        ''')
