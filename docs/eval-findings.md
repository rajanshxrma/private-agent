# Apple's on-device model computes dates wrong on 89% of tool calls

While building [private-agent](https://github.com/rajanshxrma/private-agent) — a macOS menu bar assistant that runs entirely on Apple's on-device Foundation Models — I ran a 63-trial eval across its four tools (file search, calendar, reminders, mail) instead of the handful of manual spot-checks most side projects settle for. It caught a bug invisible at small sample sizes: **the on-device model gets its own due dates wrong on 89% of calls that include one.**

## Not a formatting problem

The first version of this agent asked the model to return a due date as `MM/DD/YYYY`. Early manual testing showed occasional drift — the model would send back `"today"` as a literal string instead of an actual date. That looked like a prompt-engineering problem: tighten the instruction, add an example, move on.

It wasn't. Running the same request through varied phrasing — `"remind me to X"`, `"...today"`, `"...tomorrow"`, `"...next week"`, `"...in 3 days"`, `"...this weekend"` — 63 times against the real model (no mocks, every created reminder/event verified to exist then cleaned up) surfaced something different: the model wasn't just failing to format dates. It was **computing specific YYYY-MM-DD dates that were simply wrong** — months or years in the past relative to the actual system clock. It appears to reason about relative dates (`"tomorrow"`, `"next week"`) from an internal notion of "today" that doesn't match the real current date, and occasionally invented a due date even when the request had none.

A 3-5 call spot check would have reported anywhere from "looks fine" to "totally broken" depending on which calls you happened to run — small samples here are close to a coin flip. The 89% number only exists because the eval ran the same class of request enough times to stop being noise.

## The fix: don't trust the model's arithmetic

The policy that came out of this ([`_dates.py`](https://github.com/rajanshxrma/private-agent/blob/main/src/private_agent/tools/_dates.py)) is simple and, in hindsight, obvious for anything an LLM is asked to compute rather than merely transcribe:

- Recognize a small set of relative phrases (`today`, `tomorrow`, `next week`, `this weekend`, `in N days`) and compute the actual date **in code**, from the real system clock — never ask the model to do the arithmetic.
- If the model sends something already in the correct format and it isn't one of those phrases, and it's not a date the system can independently derive, **drop it** rather than act on it. A reminder with no due date is a much smaller problem than one with a confidently wrong one silently created on someone's calendar.

The lesson holds well past this one project: an LLM's tool-calling output can be syntactically perfect — right format, right field, right type — and still be substantively wrong in a way that only shows up under a real evaluation loop, not a one-off smoke test. Anything the model is computing rather than copying (dates, arithmetic, unit conversions) is worth an independent verification path before it's allowed to take an action with side effects.

## Methodology, for anyone running a similar eval

- 63 real trials, no mocked model calls, across 6 task phrasings x up to 8 due-date phrasings for reminders, plus dedicated calendar and file-search templates.
- Every artifact the agent actually created (reminders, calendar events) was verified to exist via AppleScript, then deleted — the eval leaves no trace and doesn't take the model's word for what it did.
- Full script: [`scripts/eval_agent.py`](https://github.com/rajanshxrma/private-agent/blob/main/scripts/eval_agent.py).

## Addendum: the same bug, in a form the safety net couldn't catch

Adding a second, faster tool-calling backend (a local MLX model, alongside the on-device one) turned up a variant of this exact bug that's worse in one specific way: the on-device model's wrong dates were usually also wrong in *format*, which is precisely what `_dates.py`'s trust check was built to reject. Running the same 63-trial task set through MLX's tool-calling (`scripts/eval_mlx_tools.py`) found tool-selection just as reliable as the on-device model (100%, 0% hallucinated tool names) — but of the calls where MLX computed its own relative date into an already-valid `MM/DD/YYYY` string, **0/36 were numerically correct**. "Tomorrow" from a real Monday came back as a date four or five days out; "next Monday" came back as a Sunday.

Because those dates are already in the trusted format, `_dates.py`'s existing policy — trust anything that looks like `MM/DD/YYYY`, reject anything else — has no way to catch them. They'd sail through as a legitimate user-provided date and land a real, wrong-dated reminder or event on the calendar, silently.

One mitigation was tried: adding an explicit instruction to MLX's system prompt telling it to pass relative phrases (`tomorrow`, `next week`, ...) through unchanged rather than computing an absolute date itself. Re-running the eval showed it helped shift more calls into the safe, code-computed path (format "drift" — now actually the desired outcome — rose from 53% to 80%), but did nothing for the remaining calls: the value-correctness number held at 0/36. The model still self-computes a date some of the time, and it's still always wrong when it does.

Given that, `run_mlx_tools` exists as a real, tested capability (see `src/private_agent/router.py`) but isn't the default for `create_reminder`/`create_calendar_event` — only for tools with no date argument, like `search_files`, where it measured cleanly. The broader lesson from the first finding held exactly where it mattered most: a second model, a second architecture, and the same category of bug, this time hidden one layer deeper.

Source for the whole project, including this fix, is at [github.com/rajanshxrma/private-agent](https://github.com/rajanshxrma/private-agent).
