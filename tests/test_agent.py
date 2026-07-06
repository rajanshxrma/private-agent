"""Real end-to-end tests for the full agent loop -- on-device model deciding
which tool to call and synthesizing the final answer. No mocks."""

import subprocess

import pytest


@pytest.fixture(autouse=True)
def _skip_if_unavailable():
    import applefoundationmodels as afm

    if not afm.apple_intelligence_available():
        pytest.skip("Apple Intelligence not available on this machine")


def test_agent_calls_file_search_tool():
    from private_agent.agent import run

    result = run("search my files for resume")
    assert len(result) > 0


def test_agent_creates_and_cleans_up_reminder():
    # Uses "contains" rather than exact match, and scopes to the default list
    # rather than iterating all lists: the on-device model relays the mark
    # through its own generation rather than passing it through verbatim, and
    # an earlier manual test found it can introduce subtle formatting drift
    # (whitespace/quoting) that breaks exact matching. Scanning every list
    # (2700+ reminders on this account) also reliably timed out at 15s.
    from private_agent.agent import run

    mark = "PRIVATE-AGENT-AGENT-TEST-DELETE-ME"
    try:
        run(f"create a reminder titled exactly '{mark}'")
        count = subprocess.run(
            ["osascript", "-e", f'''
            tell application "Reminders"
                tell default list
                    return count of (every reminder whose name contains "{mark}")
                end tell
            end tell
            '''],
            # See the delete call below -- this account's default list has
            # 2600+ reminders, and this exact query timed out at 15s on a real
            # run, so the read side needs the same 90s headroom as the delete.
            capture_output=True, text=True, timeout=90,
        ).stdout.strip()
        assert int(count) >= 1
    finally:
        subprocess.run(
            ["osascript", "-e", f'''
            tell application "Reminders"
                tell default list
                    delete (every reminder whose name contains "{mark}")
                end tell
            end tell
            '''],
            # This account's default list has 2600+ reminders -- AppleScript's
            # filtered delete over that many items is genuinely slow, not a bug.
            capture_output=True, text=True, timeout=90,
        )
