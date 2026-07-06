"""Multi-turn conversation state across successive calls in the same session.

The on-device tool-calling backend (ChatAppleFoundationModels) tracks history
itself, inside Apple's own on-device Session -- the provider's own code notes
that replaying LangChain message history does nothing, since only a leading
SystemMessage and the trailing HumanMessage are ever read from what's passed
to invoke(). So multi-turn memory for that backend means reusing the same
bound agent instance across turns (its Session persists across calls as long
as the same tools list identity is reused), never rebuilding it mid-conversation.

MLX has no equivalent persistent session, so its multi-turn memory works the
conventional way: replay the full message history through the chat template
on every call (see router.run_mlx).

Once a conversation uses the tool backend even once, every later turn in it
keeps using that backend, even for a message with no tool keyword -- "make it
3pm instead" has no keyword to route on, but only the on-device model's
session has the context (and the tools) to act on a follow-up like that.
Starting on MLX and later needing a tool mid-conversation is a known gap: the
on-device model has no visibility into what was said on MLX, since they're
different models with no shared memory.
"""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from private_agent.agent import build_agent
from private_agent.router import needs_tools, run_mlx


class Conversation:
    """Holds state for one multi-turn session. Create a new one to start fresh."""

    def __init__(self) -> None:
        self._agent = None  # built lazily, once, on the first tool-needing turn
        self._mlx_history: list[BaseMessage] = []
        self._used_tools = False

    def ask(self, prompt: str) -> str:
        if self._used_tools or needs_tools(prompt):
            self._used_tools = True
            if self._agent is None:
                self._agent = build_agent()
            result = self._agent.invoke(prompt)
            return result.content

        answer = run_mlx(prompt, history=self._mlx_history)
        self._mlx_history.append(HumanMessage(prompt))
        self._mlx_history.append(AIMessage(answer))
        return answer
