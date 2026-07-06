"""Menu bar shell for the private agent, built with rumps."""

import threading

import rumps

from private_agent.agent import run


class PrivateAgentApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Private Agent", icon=None, title="\U0001f916")

    @rumps.clicked("Ask...")
    def ask(self, _sender: rumps.MenuItem) -> None:
        window = rumps.Window(
            message="What do you need?",
            title="Private Agent",
            default_text="",
            ok="Ask",
            cancel="Cancel",
            dimensions=(320, 60),
        )
        response = window.run()
        if not response.clicked or not response.text.strip():
            return

        prompt = response.text.strip()
        self.title = "⏳"

        def _run_and_show() -> None:
            try:
                answer = run(prompt)
            except Exception as exc:  # surfaced to the user, not swallowed
                answer = f"Something went wrong: {exc}"
            rumps.alert(title="Private Agent", message=answer)
            self.title = "\U0001f916"

        threading.Thread(target=_run_and_show, daemon=True).start()

    @rumps.clicked("Quit")
    def quit_app(self, _sender: rumps.MenuItem) -> None:
        rumps.quit_application()


def main() -> None:
    PrivateAgentApp().run()


if __name__ == "__main__":
    main()
