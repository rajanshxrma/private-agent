"""Mail draft tool via AppleScript (Mail.app) -- creates drafts only, never sends."""

import subprocess

from private_agent.tools._applescript import escape


def draft_email(to: str, subject: str, body: str) -> str:
    """Create a draft email in Mail.app. This never sends the email -- it only
    creates a draft for the user to review and send themselves.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body text.
    """
    to_e, subject_e, body_e = escape(to), escape(subject), escape(body)
    script = f'''
    tell application "Mail"
        set newMessage to make new outgoing message with properties {{subject:"{subject_e}", content:"{body_e}", visible:true}}
        tell newMessage
            make new to recipient at end of to recipients with properties {{address:"{to_e}"}}
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
        return f"Failed to create draft: {result.stderr.strip()}"
    return f"Created a draft email to {to} with subject '{subject}'. It has not been sent -- review it in Mail."
