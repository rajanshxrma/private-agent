"""Real eval for MLX tool-calling: same task set as eval_agent.py, so the
tool-selection numbers are directly comparable to the on-device model's.

Runs real prompts through the real local MLX model -- no mocks. Measures
tool *selection* only (via _generate_mlx_tool_call, which parses but doesn't
execute), not full end-to-end execution -- argument coercion and real tool
execution were already verified separately (see tests/test_router.py), and
running all 63 trials through real execution would create that many real
reminders/events for a question this eval doesn't need answered. The
cleanup call at the end is a safety net in case that changes later, not
because this version creates anything.

Usage: python3 scripts/eval_mlx_tools.py
"""

import re
import time

from private_agent.router import _generate_mlx_tool_call
# _in_days/_next_weekday are "private" only by underscore convention -- reusing
# them here (rather than re-deriving "next week" or "this weekend" some other
# way) keeps this eval's ground truth for what those phrases mean identical to
# what _dates.py itself computes, since that's the definition that actually
# matters here.
from private_agent.tools._dates import _in_days, _next_weekday
from scripts.eval_agent import (
    CALENDAR_TEMPLATES,
    FILE_SEARCH_TEMPLATES,
    MARK,
    REMINDER_TEMPLATES,
    TASKS,
    cleanup_calendar_events,
    cleanup_reminders,
)

_KNOWN_TOOLS = {"search_files", "create_calendar_event", "create_reminder", "draft_email"}

# Maps each date-bearing template to the correct value for its relative
# phrase, using _dates.py's own definitions -- None means "no fixed
# expectation" (either no date implied, or an explicit date already given
# in the template, which is correct by construction and not checked here).
_EXPECTED_DATE_BY_TEMPLATE = {
    "remind me to {task} today": lambda: _in_days(0),
    "remind me to {task} tomorrow": lambda: _in_days(1),
    "remind me to {task} next week": lambda: _in_days(7),
    "set a reminder to {task} in 3 days": lambda: _in_days(3),
    "remind me to {task} this weekend": lambda: _next_weekday(5),
    "add a calendar event: {task}, tomorrow at 10am": lambda: _in_days(1),
    "put {task} on my calendar for next Monday": lambda: _next_weekday(0),
}


def _looks_like_mm_dd_yyyy(s: str) -> bool:
    return bool(re.match(r"^\d{2}/\d{2}/\d{4}$", s))


def run_trial(prompt: str, expected_tool: str, template: str = "") -> dict:
    t0 = time.time()
    call = _generate_mlx_tool_call(prompt)
    dt = time.time() - t0

    if call is None:
        return {
            "prompt": prompt,
            "expected_tool": expected_tool,
            "latency": dt,
            "called_expected_tool": False,
            "hallucinated_tool": False,
            "date_arg": None,
            "date_value_correct": None,
        }

    name = call.get("name")
    args = call.get("parameters") or {}
    date_arg = args.get("due_date") or args.get("start_date")

    date_value_correct = None
    expected_fn = _EXPECTED_DATE_BY_TEMPLATE.get(template)
    if expected_fn is not None and date_arg is not None:
        date_value_correct = _looks_like_mm_dd_yyyy(date_arg) and date_arg == expected_fn()

    return {
        "prompt": prompt,
        "expected_tool": expected_tool,
        "latency": dt,
        "called_expected_tool": name == expected_tool,
        "hallucinated_tool": name not in _KNOWN_TOOLS,
        "date_arg": date_arg,
        "date_value_correct": date_value_correct,
    }


def main():
    trials = []

    print("=== Reminder trials (date-format drift focus) ===")
    for template in REMINDER_TEMPLATES:
        for task in TASKS:
            prompt = template.format(task=f"{task} [{MARK}]")
            print(f"[{len(trials) + 1}] {prompt!r}")
            trial = run_trial(prompt, "create_reminder", template=template)
            trials.append(trial)
            print(
                f"  -> called={trial['called_expected_tool']} "
                f"hallucinated={trial['hallucinated_tool']} "
                f"date_arg={trial.get('date_arg')!r} "
                f"date_correct={trial.get('date_value_correct')} latency={trial['latency']:.2f}s"
            )

    print()
    print("=== Calendar trials ===")
    for template in CALENDAR_TEMPLATES:
        for task in TASKS[:3]:
            prompt = template.format(task=f"{task} [{MARK}]")
            print(f"[{len(trials) + 1}] {prompt!r}")
            trial = run_trial(prompt, "create_calendar_event", template=template)
            trials.append(trial)
            print(
                f"  -> called={trial['called_expected_tool']} "
                f"hallucinated={trial['hallucinated_tool']} "
                f"date_arg={trial.get('date_arg')!r} "
                f"date_correct={trial.get('date_value_correct')} latency={trial['latency']:.2f}s"
            )

    print()
    print("=== File search trials ===")
    for template in FILE_SEARCH_TEMPLATES:
        for task in TASKS[:2]:
            prompt = template.format(task=task) if "{task}" in template else template
            print(f"[{len(trials) + 1}] {prompt!r}")
            trial = run_trial(prompt, "search_files")
            trials.append(trial)
            print(
                f"  -> called={trial['called_expected_tool']} "
                f"hallucinated={trial['hallucinated_tool']} latency={trial['latency']:.2f}s"
            )

    # --- Summary ---
    n = len(trials)
    called = sum(t["called_expected_tool"] for t in trials)
    hallucinated = sum(t["hallucinated_tool"] for t in trials)

    reminder_calendar_trials = [t for t in trials if t["expected_tool"] in ("create_reminder", "create_calendar_event")]
    date_args = [t["date_arg"] for t in reminder_calendar_trials if t.get("date_arg")]
    non_standard = [d for d in date_args if not _looks_like_mm_dd_yyyy(d)]

    print()
    print(f"=== Results ({n} trials total) ===")
    print(f"Overall tool-selection success rate: {called}/{n} ({100 * called / n:.0f}%)")
    print(f"Hallucinated (unknown) tool name: {hallucinated}/{n} ({100 * hallucinated / n:.0f}%)")
    if date_args:
        print(
            f"Date-format drift: {len(non_standard)}/{len(date_args)} "
            f"({100 * len(non_standard) / len(date_args):.0f}%) of reminder/calendar "
            f"tool calls sent a non-MM/DD/YYYY date"
        )
        print(f"Non-standard values seen: {sorted(set(non_standard))}")

    # Format compliance isn't the same as being right -- a self-computed date
    # that happens to already look like MM/DD/YYYY passes _dates.py's own
    # trust check untouched, so this is measuring what that check can't see.
    checked = [t for t in reminder_calendar_trials if t.get("date_value_correct") is not None]
    if checked:
        correct = sum(t["date_value_correct"] for t in checked)
        print(
            f"Date VALUE correctness (of well-formatted, checkable dates): "
            f"{correct}/{len(checked)} ({100 * correct / len(checked):.0f}%) -- "
            f"format-compliant but numerically wrong dates are invisible to "
            f"_dates.py's current trust check"
        )

    by_tool = {}
    for t in trials:
        by_tool.setdefault(t["expected_tool"], []).append(t["called_expected_tool"])
    print()
    print("Per-tool breakdown:")
    for tool_name, results in by_tool.items():
        print(f"  {tool_name}: {sum(results)}/{len(results)} ({100 * sum(results) / len(results):.0f}%)")

    latencies = [t["latency"] for t in trials]
    print()
    print(f"Median latency: {sorted(latencies)[len(latencies) // 2]:.2f}s")

    print()
    print("Cleaning up eval artifacts (in case any hallucinated calls partially succeeded)...")
    print(f"Reminders deleted: {cleanup_reminders(MARK)}")
    print(f"Calendar events deleted: {cleanup_calendar_events(MARK)}")


if __name__ == "__main__":
    main()
