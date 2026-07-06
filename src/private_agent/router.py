"""Routes each prompt to the backend best suited for it.

Two backends are available, with very different tradeoffs (measured, see
README benchmarks): the on-device Apple Foundation Model has real, tested
tool-calling support (via langchain-apple-foundation-models) but a ~6.6s
median response; a local MLX-served model responds in ~1.3s but has no
tool-execution loop built for it here.

MLX's chat template *can* express tool-call prompts (Llama-3.2's template
accepts a `tools` argument and asks the model to emit a JSON function call),
but using that would mean parsing potentially malformed JSON and building a
full tool-execution round trip from scratch. That's real, doable work for a
future version -- not something to bolt on under time pressure this round.
So the router is deliberately simple: if the prompt looks like it needs one
of this agent's real tools, use the backend that actually has tool-calling
wired up. Otherwise, use the faster backend for a plain answer.
"""

import re

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
