"""Routes each prompt to the backend best suited for it.

Two backends are available, with very different tradeoffs (measured, see
README benchmarks): the on-device Apple Foundation Model has real, tested
tool-calling support (via langchain-apple-foundation-models) but a ~6.6s
median response; a local MLX-served model responds in ~3.7s for a tool call
and picks the right tool just as reliably (100% on the same 63-trial task
set as the on-device eval, 0% hallucinated tool names) -- but its date
arguments are a real, measured safety problem the on-device path doesn't
have: 0/36 self-computed relative dates (tomorrow, next week, this weekend,
etc.) were numerically correct in real testing, and because they're already
in valid MM/DD/YYYY format, `_dates.py`'s trust check can't catch them the
way it catches the on-device model's format drift. See docs/eval-findings.md
for the full numbers and the (partially effective) prompt-level mitigation
that was tried.

Given that, the router still sends every tool-needing prompt to the
on-device backend by default. `run_mlx_tools` exists as a real, tested,
faster alternative for tools with no date argument (search_files had 100%
accuracy with no measured downside), with a documented failure contract
(returns None rather than a hallucinated action) -- but isn't wired in as
the default for create_reminder/create_calendar_event given the date-value
gap above.
"""

import json
import re
from typing import Optional

from langchain_core.tools import tool as _as_tool
from langchain_core.utils.function_calling import convert_to_openai_tool

from private_agent.tools import create_calendar_event, create_reminder, draft_email, search_files

# Keywords mapped to what they'd need a tool for. Deliberately simple keyword
# matching, not a classifier call -- an extra LLM call just to decide routing
# would spend more time than the routing saves.
_TOOL_KEYWORDS = {
    "search_files": ["find", "search", "look for", "where is", "where's", "locate"],
    "create_calendar_event": ["calendar", "schedule", "meeting", "appointment"],
    "create_reminder": ["remind", "reminder", "to-do", "todo"],
    "draft_email": ["email", "e-mail", "draft", "mail to", "send a message"],
}

_ALL_KEYWORDS = [kw for kws in _TOOL_KEYWORDS.values() for kw in kws]


def needs_tools(prompt: str) -> bool:
    """Whether this prompt plausibly needs one of this agent's real tools."""
    lowered = prompt.lower()
    return any(re.search(rf"\b{re.escape(kw)}\b", lowered) for kw in _ALL_KEYWORDS)


_mlx_model = None
_mlx_tokenizer = None


def _get_mlx():
    global _mlx_model, _mlx_tokenizer
    if _mlx_model is None:
        from mlx_lm import load

        _mlx_model, _mlx_tokenizer = load("mlx-community/Llama-3.2-3B-Instruct-4bit")
    return _mlx_model, _mlx_tokenizer


_MLX_SYSTEM_PROMPT = (
    "You are a concise, direct private assistant running entirely on this Mac. "
    "Answer plainly without excessive hedging or filler."
)

_MLX_TOOLS_SYSTEM_PROMPT = (
    _MLX_SYSTEM_PROMPT
    + " When a due_date or start_date argument corresponds to a relative phrase "
    "in the user's request (today, tomorrow, next week, this weekend, in N days), "
    "pass that exact phrase through as the argument value, unchanged -- never "
    "calculate or invent an absolute date yourself. Only send an absolute "
    "MM/DD/YYYY date if the user's request already stated one explicitly."
)


def run_mlx(prompt: str, history: list | None = None, max_tokens: int = 256) -> str:
    """Plain (non-tool) generation via the faster local MLX model.

    Uses a shorter, accurate system prompt rather than agent.py's
    INSTRUCTIONS -- that one describes tool capabilities (file search,
    calendar, reminders, mail) this no-tools path can't actually act on, so
    reusing it here would promise something this path can't deliver.

    MLX has no persistent session of its own (unlike the on-device backend --
    see conversation.py), so multi-turn memory here means literally replaying
    prior turns through the chat template on every call. `history` is a list
    of langchain_core HumanMessage/AIMessage in chronological order.
    """
    from mlx_lm import generate

    model, tokenizer = _get_mlx()
    messages = [{"role": "system", "content": _MLX_SYSTEM_PROMPT}]
    for msg in history or []:
        role = "assistant" if msg.type == "ai" else "user"
        messages.append({"role": role, "content": msg.content})
    messages.append({"role": "user", "content": prompt})
    formatted = tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
    return generate(model, tokenizer, prompt=formatted, max_tokens=max_tokens, verbose=False)


# LangChain-wrapped (not raw) tool functions -- StructuredTool.invoke() gets
# real argument coercion (a real eval run had the model send "limit": "10"
# as a string for an int field; raw **kwargs would crash on that) and a
# catchable ValidationError on genuinely bad/missing args, for free.
_MLX_TOOLS = {
    f.__name__: _as_tool(f)
    for f in (search_files, create_calendar_event, create_reminder, draft_email)
}

_mlx_tool_schemas = None


def _get_mlx_tool_schemas():
    global _mlx_tool_schemas
    if _mlx_tool_schemas is None:
        _mlx_tool_schemas = [convert_to_openai_tool(t) for t in _MLX_TOOLS.values()]
    return _mlx_tool_schemas


def _generate_mlx_tool_call(prompt: str, max_tokens: int = 200) -> Optional[dict]:
    """Ask the MLX model for a tool call and parse it, without executing anything.

    Returns the raw `{"name": ..., "parameters": {...}}` dict if the model's
    output parses as one (regardless of whether "name" is actually one of
    this agent's real tools), or None if it isn't even valid JSON shaped like
    a call. Split out from run_mlx_tools so eval code can score *what the
    model tried to call* separately from whether execution succeeded.
    """
    from mlx_lm import generate

    model, tokenizer = _get_mlx()
    messages = [
        {"role": "system", "content": _MLX_TOOLS_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    formatted = tokenizer.apply_chat_template(
        messages, tools=_get_mlx_tool_schemas(), add_generation_prompt=True, tokenize=False
    )
    raw = generate(model, tokenizer, prompt=formatted, max_tokens=max_tokens, verbose=False)

    try:
        call = json.loads(raw.strip())
    except (ValueError, TypeError):
        return None
    if not isinstance(call, dict) or "name" not in call:
        return None
    return call


def _execute_mlx_tool_call(call: dict) -> Optional[str]:
    """Execute an already-parsed {"name": ..., "parameters": {...}} dict, or
    return None if it doesn't name one of this agent's real tools or its
    arguments fail validation. Split out from run_mlx_tools so this guard
    logic -- the part that's actually ours, not the model's -- can be tested
    directly with a hand-built dict instead of waiting on a live model to
    reproduce a specific (and non-deterministic) failure on demand.
    """
    lc_tool = _MLX_TOOLS.get(call.get("name"))
    if lc_tool is None:
        return None
    try:
        return lc_tool.invoke(call.get("parameters") or {})
    except Exception:
        return None


def run_mlx_tools(prompt: str, max_tokens: int = 200) -> Optional[str]:
    """Attempt tool-calling via the local MLX model instead of the on-device one.

    Llama-3.2's chat template accepts a `tools` argument, and real testing
    shows it reliably emits a `{"name": ..., "parameters": {...}}` JSON object
    rather than prose once tools are bound -- but not always a *correct* one.
    A 3B model bound with tools is eager to call one even for prompts that
    don't need any, and real testing found two distinct failure shapes for
    that: a hallucinated tool name outside this agent's real four (asked
    "what is 2+2", got back a call to a nonexistent tool named "eval"), and
    -- on a different sampling of the same prompt -- a real, known tool
    called for a completely unrelated purpose (the same math question
    triggering a real search_files call). The first fails closed for free
    (unknown name, never executed); the second is a real action taken for
    the wrong reason, and this function alone can't distinguish "used
    correctly" from "used confidently but wrong" -- see docs/eval-findings.md
    for the measured rate and why the router doesn't default to this path.

    Returns None (never raises) on anything that isn't a clean, valid call to
    one of this agent's real tools -- malformed JSON, an unknown tool name, or
    arguments that fail validation -- so the caller can fall back to the
    on-device backend instead of surfacing a hallucinated action as if it
    were real. It cannot and does not catch a valid call to the wrong tool.
    """
    call = _generate_mlx_tool_call(prompt, max_tokens=max_tokens)
    if call is None:
        return None
    return _execute_mlx_tool_call(call)
