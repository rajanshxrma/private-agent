"""Simple CLI entrypoint for testing the agent without the menu bar shell."""

import sys

from private_agent.agent import run


def main() -> None:
    prompt = " ".join(sys.argv[1:])
    if not prompt:
        print("Usage: private-agent <prompt>")
        sys.exit(1)
    print(run(prompt))


if __name__ == "__main__":
    main()
