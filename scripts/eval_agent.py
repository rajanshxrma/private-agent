"""Real evals for private-agent: tool-selection accuracy across all 4 tools,
plus a focused look at date-format drift (the exact surface a real bug was
found on earlier).

Runs real prompts through the real on-device model -- no mocks. Every
artifact created (reminders, calendar events) is verified to exist, then
cleaned up, matching the discipline used throughout this project's manual
testing. Mail is excluded from the loop (opens a visible compose window per
call; same reasoning as tests/test_tools.py's skip).

Usage: python3 scripts/eval_agent.py
"""

import re
import subprocess
import time

from private_agent.agent import build_agent

MARK = "EVAL-DELETE-ME"

TASKS = [
    "call the dentist",
    "submit the assignment",
    "buy groceries",
    "review the PR",
    "follow up with the recruiter",
    "renew the passport",
]

# Deliberately varied due-date phrasing -- this is the exact surface where a
# real bug was found earlier (the model sending "today" instead of the
# documented MM/DD/YYYY format). Varying phrasing measures how often that
# drift actually happens, not just whether one hardcoded phrasing works.
REMINDER_TEMPLATES = [
    "remind me to {task}",
    "remind me to {task} today",
    "remind me to {task} tomorrow",
    "remind me to {task}, due 07/15/2026",
    "remind me to {task} next week",
    "set a reminder to {task} in 3 days",
    "remind me to {task} this weekend",
    "create a reminder: {task}",
]

CALENDAR_TEMPLATES = [
    "schedule a meeting about {task} for 07/20/2026 at 2pm",
    "add a calendar event: {task}, tomorrow at 10am",
    "put {task} on my calendar for next Monday",
]

FILE_SEARCH_TEMPLATES = [
    "find my resume",
    "search for files about {task}",
    "where is my base_resume file",
]


def _osascript(script: str, timeout: int = 90) -> str:
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        # The original bug here: only ever looking at stdout meant a real
        # AppleScript error (e.g. deleting a large whose-clause result)
        # silently returned an empty string instead of surfacing the
        # failure -- 18 reminders and 2 calendar events were left orphaned
        # in a real run because of this. Fail loudly instead.
        raise RuntimeError(f"osascript failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def cleanup_reminders(mark: str) -> str:
    # AppleScript's `delete` errors (-1700, "Can't make {} into type
    # specifier") on an empty whose-clause result -- guard with an if so a
    # zero-match cleanup is a no-op instead of a crash.
    return _osascript(f'''
    tell application "Reminders"
        tell default list
            set matches to (every reminder whose name contains "{mark}")
            set n to count of matches
            if n > 0 then delete matches
            return n
        end tell
    end tell
    ''')


def cleanup_calendar_events(mark: str) -> str:
    return _osascript(f'''
    tell application "Calendar"
        tell calendar 1
            set matches to (every event whose summary contains "{mark}")
            set n to count of matches
            if n > 0 then delete matches
            return n
        end tell
    end tell
    ''')


def _looks_like_mm_dd_yyyy(s: str) -> bool:
    return bool(re.match(r"^\d{2}/\d{2}/\d{4}$", s))


def run_trial(prompt: str, expected_tool: str) -> dict:
    agent = build_agent()
    t0 = time.time()
    try:
        result = agent.invoke(prompt)
    except Exception as exc:  # a crash is a failure, not something to hide
        return {
            "prompt": prompt,
            "expected_tool": expected_tool,
            "latency": time.time() - t0,
            "called_expected_tool": False,
            "error": str(exc),
            "date_arg": None,
        }

    dt = time.time() - t0
    tool_calls = result.tool_calls or []
    called_expected = any(tc["name"] == expected_tool for tc in tool_calls)
    date_arg = None
    for tc in tool_calls:
        if tc["name"] == expected_tool:
            date_arg = tc["args"].get("due_date") or tc["args"].get("start_date")

    return {
        "prompt": prompt,
        "expected_tool": expected_tool,
        "latency": dt,
        "called_expected_tool": called_expected,
        "date_arg": date_arg,
        "response": result.content,
        "error": None,
    }


def main():
    trials = []

    print("=== Reminder trials (date-format drift focus) ===")
    for i, template in enumerate(REMINDER_TEMPLATES):
        for j, task in enumerate(TASKS):
            prompt = template.format(task=f"{task} [{MARK}]")
            print(f"[{len(trials) + 1}] {prompt!r}")
            trial = run_trial(prompt, "create_reminder")
            trials.append(trial)
            print(
                f"  -> called={trial['called_expected_tool']} "
                f"date_arg={trial.get('date_arg')!r} latency={trial['latency']:.2f}s"
            )

    print()
    print("=== Calendar trials ===")
    for i, template in enumerate(CALENDAR_TEMPLATES):
        for task in TASKS[:3]:
            prompt = template.format(task=f"{task} [{MARK}]")
            print(f"[{len(trials) + 1}] {prompt!r}")
            trial = run_trial(prompt, "create_calendar_event")
            trials.append(trial)
            print(
                f"  -> called={trial['called_expected_tool']} "
                f"date_arg={trial.get('date_arg')!r} latency={trial['latency']:.2f}s"
            )

    print()
    print("=== File search trials ===")
    for template in FILE_SEARCH_TEMPLATES:
        for task in TASKS[:2]:
            prompt = template.format(task=task) if "{task}" in template else template
            print(f"[{len(trials) + 1}] {prompt!r}")
            trial = run_trial(prompt, "search_files")
            trials.append(trial)
            print(f"  -> called={trial['called_expected_tool']} latency={trial['latency']:.2f}s")

    # --- Summary ---
    n = len(trials)
    called = sum(t["called_expected_tool"] for t in trials)
    errors = sum(1 for t in trials if t.get("error"))

    reminder_calendar_trials = [t for t in trials if t["expected_tool"] in ("create_reminder", "create_calendar_event")]
    date_args = [t["date_arg"] for t in reminder_calendar_trials if t.get("date_arg")]
    non_standard = [d for d in date_args if not _looks_like_mm_dd_yyyy(d)]

    print()
    print(f"=== Results ({n} trials total) ===")
    print(f"Overall tool-selection success rate: {called}/{n} ({100 * called / n:.0f}%)")
    print(f"Errors/exceptions: {errors}/{n}")
    print(
        f"Date-format drift: {len(non_standard)}/{len(date_args)} "
        f"({100 * len(non_standard) / len(date_args):.0f}% if any date arg present) "
        f"of reminder/calendar tool calls sent a non-MM/DD/YYYY date"
    )
    print(f"Non-standard values seen: {sorted(set(non_standard))}")

    by_tool = {}
    for t in trials:
        by_tool.setdefault(t["expected_tool"], []).append(t["called_expected_tool"])
    print()
    print("Per-tool breakdown:")
    for tool_name, results in by_tool.items():
        print(f"  {tool_name}: {sum(results)}/{len(results)} ({100 * sum(results) / len(results):.0f}%)")

    print()
    print("Cleaning up eval artifacts...")
    print(f"Reminders deleted: {cleanup_reminders(MARK)}")
    print(f"Calendar events deleted: {cleanup_calendar_events(MARK)}")


if __name__ == "__main__":
    main()
