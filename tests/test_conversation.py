"""Real tests for multi-turn conversation state -- no mocks."""

import subprocess

import pytest


def test_mlx_only_conversation_carries_history():
    from private_agent.conversation import Conversation

    convo = Conversation()
    first = convo.ask("what is the capital of France, in one word")
    assert len(first) > 0
    assert not convo._used_tools

    second = convo.ask("and one more fact about that city")
    assert len(second) > 0
    assert not convo._used_tools
    # both turns replayed into the same running MLX history
    assert len(convo._mlx_history) == 4


@pytest.fixture
def _skip_if_unavailable():
    import applefoundationmodels as afm

    if not afm.apple_intelligence_available():
        pytest.skip("Apple Intelligence not available on this machine")


def test_followup_with_no_tool_keyword_still_uses_tool_backend(_skip_if_unavailable):
    from private_agent.conversation import Conversation

    mark = "PRIVATE-AGENT-CONVERSATION-TEST-DELETE-ME"
    convo = Conversation()
    try:
        convo.ask(f"create a reminder titled exactly '{mark}'")
        assert convo._used_tools

        count = subprocess.run(
            ["osascript", "-e", f'''
            tell application "Reminders"
                tell default list
                    return count of (every reminder whose name contains "{mark}")
                end tell
            end tell
            '''],
            capture_output=True, text=True, timeout=90,
        ).stdout.strip()
        assert int(count) >= 1

        # No tool keyword anywhere in this message -- this only passes if the
        # conversation keeps routing to the tool backend once it's been used
        # once, which is exactly the kind of follow-up a real user sends next.
        follow_up = convo.ask("nevermind, that one's done")
        assert len(follow_up) > 0
        assert convo._used_tools
    finally:
        subprocess.run(
            ["osascript", "-e", f'''
            tell application "Reminders"
                tell default list
                    delete (every reminder whose name contains "{mark}")
                end tell
            end tell
            '''],
            capture_output=True, text=True, timeout=90,
        )
