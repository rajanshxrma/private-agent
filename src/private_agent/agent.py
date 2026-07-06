"""Core agent: a private, on-device assistant with tool-calling access to your Mac."""

from langchain_apple_foundation_models import ChatAppleFoundationModels
from langchain_core.tools import tool

from private_agent.router import needs_tools, run_mlx
from private_agent.tools import create_calendar_event, create_reminder, draft_email, search_files

INSTRUCTIONS = (
    "You are a private, on-device assistant running entirely on this Mac. "
    "You can search the user's files, create calendar events, create reminders, "
    "and draft (never send) emails. Be concise and direct in your answers. "
    "Always confirm what action you took."
)


def build_agent() -> ChatAppleFoundationModels:
    tools = [
        tool(search_files),
        tool(create_calendar_event),
        tool(create_reminder),
        tool(draft_email),
    ]
    llm = ChatAppleFoundationModels(instructions=INSTRUCTIONS)
    return llm.bind_tools(tools)


def run(prompt: str) -> str:
    # Route by whether the prompt plausibly needs a real tool -- see
    # router.py for why this isn't a model-based classifier call.
    if not needs_tools(prompt):
        return run_mlx(prompt)
    agent = build_agent()
    result = agent.invoke(prompt)
    return result.content
