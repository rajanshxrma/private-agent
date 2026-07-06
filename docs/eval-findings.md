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

Source for the whole project, including this fix, is at [github.com/rajanshxrma/private-agent](https://github.com/rajanshxrma/private-agent).
