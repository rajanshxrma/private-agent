# private-agent

A private, on-device agentic assistant for macOS. Ask it to find a file, create a calendar event, set a reminder, or draft an email -- it decides which action to take and does it, entirely on your Mac.

![demo](assets/demo.gif)

Runs on Apple's on-device Foundation Models via [langchain-apple-foundation-models](https://github.com/rajanshxrma/langchain-apple-foundation-models) -- no API key, no network call, nothing leaves the machine.

## Install

**Menu bar app (no Python required):** grab `Private-Agent-macOS.zip` from the [latest release](https://github.com/rajanshxrma/private-agent/releases/latest), unzip, and drag `Private Agent.app` to Applications.

It's unsigned (no Apple Developer account behind this yet), so the first launch needs one extra step: right-click the app -> **Open** -> **Open** in the dialog that follows (or System Settings -> Privacy & Security -> "Open Anyway" if macOS blocks it outright). After that first launch it opens normally.

**From source:**

```
pip install -e .
private-agent "find my resume"
```

Or run it as a menu bar app:

```
private-agent-menubar
```

For voice input/output (see Voice front-end below), use `private-agent --voice` from source, or the **Ask (voice)** menu item in the menu bar app.

Requires macOS 26+ with Apple Intelligence enabled, Apple Silicon.

## What it can do

| Tool | Backing implementation |
|---|---|
| Search files by name | Spotlight (`mdfind -name`), scoped to Downloads/Documents/Desktop |
| Create a calendar event | AppleScript -> Calendar.app |
| Create a reminder | AppleScript -> Reminders.app |
| Draft an email | AppleScript -> Mail.app -- creates a draft only, **never sends** |

The on-device model decides which tool (if any) to call based on your prompt, executes it, and gives you back a plain-language confirmation of what it did.

## Architecture

```mermaid
flowchart LR
    Prompt["prompt"] --> Router{"needs a tool?<br/>(router.py)"}
    Router -->|"yes"| LLM["ChatAppleFoundationModels<br/>(on-device model)"]
    Router -->|"no"| MLX["MLX local model<br/>(Llama-3.2-3B-4bit)"]

    LLM -->|"decides + calls"| Tools

    subgraph Tools["bind_tools([...])"]
        direction TB
        T1["search_files<br/>Spotlight (mdfind)"]
        T2["create_calendar_event<br/>AppleScript -> Calendar.app"]
        T3["create_reminder<br/>AppleScript -> Reminders.app"]
        T4["draft_email<br/>AppleScript -> Mail.app (draft only)"]
    end

    Tools --> Result["real result"]
    Result --> LLM
    LLM --> Answer["synthesized natural-language answer"]
    MLX --> Answer
```

Each tool is a plain Python function; the on-device model's tool-calling introspects the function signature and docstring to know what arguments to pass -- no manual JSON-schema authoring.

### Why there's a router

The two backends have very different tradeoffs (see benchmarks below): the on-device model has real, tested tool-calling; MLX is faster and, as of `run_mlx_tools`, has a real working tool-execution path too -- Llama-3.2's chat template accepts a `tools` argument, and a 63-trial eval (same task set as the on-device one, see Evals below) found MLX just as accurate at picking the right tool (100%, 0% hallucinated tool names). What it isn't reliable at yet is dates: 0/36 self-computed relative dates were numerically correct, and because they come back in valid `MM/DD/YYYY` format, `_dates.py`'s trust check can't catch them the way it catches the on-device model's format drift. See [`docs/eval-findings.md`](docs/eval-findings.md) for the full numbers.

So `router.py` uses a deliberately simple keyword classifier: if the prompt plausibly needs one of this agent's real tools (mentions finding/searching, scheduling, reminders, or email), it goes to the on-device model. Otherwise, it goes to MLX for a faster plain answer. No extra LLM call to decide routing -- that would spend more time than the routing saves. `run_mlx_tools` exists as a faster, tested alternative for tools with no date argument (`search_files` measured cleanly), but isn't wired in as the default for date-bearing tools given the gap above.

## Multi-turn conversations

The menu bar app remembers context across successive "Ask..." calls, so a follow-up like "actually, make it 3pm instead" works without repeating the whole request. Use the **New Conversation** menu item to drop that context and start clean.

The two backends carry that memory very differently, because of how each one actually works:

- **On-device (tool) backend:** `ChatAppleFoundationModels` tracks conversation history itself, inside Apple's own on-device Session object -- replaying LangChain message history back into it does nothing, since the provider only ever reads a leading system message and the final human message off what's passed to `invoke()`. So `conversation.py` gets multi-turn memory here for free, just by reusing the same bound agent instance across turns instead of rebuilding it per call.
- **MLX backend:** has no persistent session of its own, so memory here means literally replaying the full prior exchange through the chat template on every call (`router.run_mlx`'s `history` argument).

One consequence of the on-device model owning its own history: once a conversation uses a real tool even once, every later turn in that conversation keeps going to the on-device backend, even if a later message has no tool keyword at all ("nevermind, that one's done" has nothing for the router to match on, but only the on-device session has the context to act on it). See Known limitations below for what this doesn't cover.

## Voice front-end -- the AusCall-latency-discipline sequel

`private-agent --voice` (or **Ask (voice)** in the menu bar app) speaks your request and hears the answer read back, entirely on-device -- Apple's `Speech` framework for recognition (`SFSpeechRecognizer`, `requiresOnDeviceRecognition=True` set explicitly, verified this machine actually supports it rather than assumed) and `AVSpeechSynthesizer` for output, both via `voice.py`. No cloud speech API -- Apple's own docs describe speech recognition as "a network-based service" by default, which is a direct conflict with this whole project's premise, so on-device is forced rather than left as the default.

**Three real bugs found and fixed while building this, worth understanding before touching `voice.py` again:**

1. **A live-streaming design that silently never worked.** The first version streamed microphone buffers straight into a `SFSpeechAudioBufferRecognitionRequest` and polled for a result with plain `time.sleep()`. The audio tap fired reliably (confirmed: 61 real buffer callbacks in 6s of real audio) -- but the recognition task's result callback never fired even once. PyObjC/Cocoa async callbacks like this are delivered via the run loop, and a bare `time.sleep()` loop never services it. Fixed by switching to record-to-file (pure tap -> disk I/O, no callback timing involved) followed by one `SFSpeechURLRecognitionRequest` on the finished file, pumping `NSRunLoop` only for that one short request.
2. **A race between the recording write finishing and the transcription read starting.** The exact same recording transcribed perfectly when read moments later in a fresh, separate call -- but returned empty when `listen()` called the transcribe step immediately after recording in the same process. `AVAudioFile` was never explicitly `.close()`d, only left to fall out of scope; Python/PyObjC's deallocation timing isn't guaranteed to happen before the very next line runs. Fixed with an explicit `.close()`.
3. **Amplitude-based silence detection (stop recording once it goes quiet) proved genuinely unreliable across repeated real testing** -- 1/5 consecutive trials succeeded at one point, with most either cutting off before speech was ever detected or never detecting trailing silence at all. The amplitude math itself was verified correct in isolation (real speech measured at 0.17-0.43 peak vs. a 0.015-0.024 ambient noise floor, a clean order of magnitude apart) -- something about repeated `AVAudioEngine` start/stop cycles made the *live* behavior inconsistent in ways not worth chasing further blind. Replaced with fixed-duration recording (always record for `_RECORD_SECONDS = 5`, then transcribe the complete file), which removed the whole failure class: 5/5 on the first real test after the switch.

**Real, measured reliability -- stated honestly, not oversold:** across ~25 real test trials (synthesized speech via macOS's `say`, picked up by the actual microphone, transcribed by the actual on-device recognizer), single-attempt success ranged 67-87% depending on the batch. Failures split into two different kinds: genuine silence/nothing-heard (which a retry reliably fixes) and outright misrecognition -- the recognizer confidently transcribing the wrong short word ("Sachin" for "testing", "Siri" for "draft an email"). `listen()` retries automatically up to 3 attempts total on an empty result, which pushes "at least one attempt returns *something*" past 99% at the measured per-attempt rate -- but a retry can't fix misrecognition, since a non-empty (if wrong) result stops the retry loop. This is a real, honest limitation of speech recognition technology generally (no STT system, including Apple's own Siri/Dictation, is 100%), likely made *worse* here than real usage would show: this session's own testing is a Mac's speaker bouncing synthesized audio back into its own microphone, which is measurably noisier than a human voice spoken directly at the laptop from normal distance. Real human-voice reliability is expected to be higher than these numbers, but hasn't been separately validated in this session -- worth a real spot-check before leaning on this for anything time-sensitive.

## Benchmarks (M1, 16GB RAM -- measured, not estimated)

Small samples of this on-device model were wildly inconsistent run to run (0.3s-6.8s, occasional hangs) with no clear cause. A 20-call single-session sample resolved it into a real, repeatable number:

| Backend | Median | Mean | Range | Sample |
|---|---|---|---|---|
| Apple on-device Foundation Model | 6.58s | 6.29s | 3.52s - 6.78s | 20 calls, short prompt |
| MLX local (`Llama-3.2-3B-Instruct-4bit`) | 1.34s | 1.40s | 1.27s - 1.68s | 10 calls, 50 tokens |

MLX is meaningfully faster and far more consistent for this workload on this hardware -- worth factoring in if latency matters more than using Apple's own on-device model specifically. The on-device Foundation Model's 20-call sample also showed 2 consecutive unexplained faster outliers (~3.5s) breaking an otherwise tight ~6.5-6.6s cluster; small samples (3-5 calls) would have reported anywhere from 0.3s to a full timeout depending on when you happened to measure.

## Evals (63 real trials, not anecdotes)

`scripts/eval_agent.py` runs real prompts through the real on-device model across all four tools, verifying and cleaning up every artifact it creates. Two things worth knowing before you trust this agent with a real date:

| Metric | Result |
|---|---|
| Overall tool-selection accuracy | 59/63 (94%) |
| `create_reminder` tool-selection accuracy | 46/48 (96%) |
| `create_calendar_event` tool-selection accuracy | 8/9 (89%) |
| `search_files` tool-selection accuracy | 5/6 (83%) |
| Date-format drift (non-MM/DD/YYYY sent for a due/start date) | 48/54 (89%) |

The date-format number is the important one. It's not just plain-language dates like "today" -- the model frequently computes its own specific `YYYY-MM-DD` date and gets it flat wrong (dates in 2024-2025 were common outputs in a July 2026 test run), and sometimes invents a due date when the prompt never mentioned one at all. `tools/_dates.py` handles this by refusing to trust anything it can't verify: a small set of relative phrases (`today`, `tomorrow`, `next week`, `this weekend`, `in N days`) get computed from the real system clock; anything else -- including a plausible-looking `YYYY-MM-DD` -- is dropped rather than risked. A reminder with no due date is a much smaller problem than one confidently set on the wrong day.

`scripts/eval_mlx_tools.py` runs the identical 63-trial task set through MLX's tool-calling, for a direct comparison:

| Metric | Result |
|---|---|
| Overall tool-selection accuracy | 63/63 (100%) |
| Hallucinated (unknown) tool name | 0/63 (0%) |
| Date-format drift (non-MM/DD/YYYY sent for a due/start date) | 37/46 (80%) |
| Date **value** correctness (of the format-compliant, checkable dates) | 0/36 (0%) |

MLX picks the right tool at least as reliably as the on-device model -- but that last row is the one that matters: every self-computed relative date that came back already in valid `MM/DD/YYYY` format was numerically wrong (e.g. "tomorrow" computed from a real Monday landed 4-5 days out), and because it's already in the trusted format, `_dates.py`'s existing safety net can't catch it the way it catches the on-device model's format drift. A system-prompt instruction telling the model to pass relative phrases through unchanged rather than compute them measurably helped (that's why format drift is *higher* here than it looks safe to want -- more calls landing in the safe, code-computed path is the good outcome), but didn't eliminate the failure for the calls that still self-compute.

Full writeup on both findings: [`docs/eval-findings.md`](docs/eval-findings.md).

## Known limitations (found by actually testing this, not guessed)

- **The on-device model can't be trusted to compute its own dates.** See Evals above -- `_dates.py`'s reject-if-unrecognized policy trades some false negatives (a legitimately unusual but correct date format gets dropped too) for eliminating silently-wrong dates, which is the worse failure mode of the two.
- **MLX tool-calling can't be trusted with dates either -- and it's harder to catch.** See Evals above -- 0/36 self-computed relative dates were correct, and unlike the on-device model's drift, they come back in already-valid `MM/DD/YYYY` format, so the existing safety net doesn't catch them. This is why `run_mlx_tools` isn't the default for `create_reminder`/`create_calendar_event`.
- **File search is scoped to Downloads/Documents/Desktop, not the whole disk.** A naive Spotlight query with no scoping returns full-text matches from every indexed file on the machine, including code comments inside installed libraries -- try searching "resume" with no scoping and you'll get pytest internals before your actual resume.
- **AppleScript automation on Reminders.app gets slow at scale.** Filtered queries and deletes against a list with 2000+ items can take tens of seconds -- this is a real characteristic of the scripting bridge at scale, not something this tool can fix.
- **Mail drafts persist even if you close the compose window without saving.** Mail.app auto-saves visible compose windows to Drafts on its own schedule -- this is actually the desired behavior (you want to find your draft later), just worth knowing if you're testing.
- **Starting a conversation on MLX, then needing a tool, loses context.** See Multi-turn conversations above -- the on-device model has no visibility into anything said on MLX, since they're different models with no shared memory. Once a conversation touches the tool backend it stays there for the rest of that conversation to avoid the reverse problem.
- **Doesn't yet use Apple's newer WWDC26 APIs** (the `LanguageModel` protocol for multi-model routing, `DynamicProfile` for multi-agent workflows, image input) -- those require a beta OS/SDK combination not yet stable enough to depend on for a working demo. Built entirely on the stable, public Foundation Models API.
- **Voice input (`listen()`) isn't 100% reliable on the first attempt.** See the Voice front-end section above for the real measured numbers and the two distinct failure modes (silence vs. misrecognition) -- a retry (up to 3 attempts) handles the silence case well but can't fix a confidently-wrong transcription.
- **Voice input records for a fixed 5-second window, not until you stop talking.** A deliberate trade-off after dynamic silence detection proved unreliable in testing (see Voice front-end above) -- a short command still waits the full window, and anything longer than 5 seconds gets cut off.

## License

MIT
