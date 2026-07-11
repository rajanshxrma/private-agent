"""On-device speech in/out for private-agent -- the AusCall-latency-discipline
sequel, using Apple's Speech and AVFoundation frameworks directly (no cloud
speech API).

Speech recognition defaults to network-based on Apple's platforms (Apple's
own docs: "speech recognition is a network-based service") -- that's a
direct conflict with this whole portfolio's "nothing leaves the machine"
premise, so this module explicitly sets `requiresOnDeviceRecognition = True`
on every request rather than relying on the default. Verified directly (not
assumed) that this machine actually supports on-device recognition
(`SFSpeechRecognizer.supportsOnDeviceRecognition()` is True here) before
writing any code against it.

ARCHITECTURE NOTE -- a real bug found and fixed during testing: the first
version of listen() streamed live audio buffers straight into a
SFSpeechAudioBufferRecognitionRequest and polled for a result with plain
time.sleep(). That silently never worked -- the audio tap fired reliably
(confirmed: 61 real buffer callbacks in 6s), but the recognition task's
result callback never fired even once, because PyObjC/Cocoa async callbacks
like this one are delivered via the run loop, and a bare time.sleep() loop
never services it. Fixed by switching to record-to-file (pure tap -> disk
I/O, no callback timing involved) followed by a single SFSpeechURLRecognitionRequest
on the finished file, pumping NSRunLoop only for that one short request --
verified end-to-end with a real recorded phrase ("the quick brown fox jumps
over the lazy dog testing 123", isFinal=True). This also sidesteps the
live-streaming reliability problem entirely rather than chasing it further.

FIXED -- macOS 27 regression found during the 2026-07-09 stability
checkpoint, fix verified closed 2026-07-10 (see the re-verification note
at the end). On that OS, listen()'s record-then-immediately-
transcribe flow originally reliably returned empty (2026-07-09: 4
consecutive full test failures, 12 total real attempts, zero successful
transcriptions) via a silent 15s stall -- the recognizer's callback simply
never fired in-process, even though _record_to_file() produced a WAV with
strong, correctly-recorded speech, and _transcribe_file() correctly
transcribed that exact file verbatim when called in a fresh, separate
process. A settle delay after close() (tried 0.2s and 1s) did NOT fix it,
ruling out a flush-timing gap. An explicit AVAudioSession teardown in
_record_to_file() (deactivating the shared session before transcription
starts) was tried next and ALSO did NOT fix it -- ruled out by direct
experiment, not guessed.

What DID fix the stall, confirmed by direct experiment: running
_transcribe_file() in an actual freshly-spawned OS process reliably
completes fast (~1.3-1.6s, not a 15s timeout) every time, immediately after
recording -- while the identical call made in-process still silently stalls
to the deadline. _record_and_transcribe_once() now runs transcription in
its own `multiprocessing` "spawn" subprocess for this reason (mirrors the
"fresh process already works" case, same pattern as lantern's native
backend probe). This part is real and verified: 2026-07-10, in a quiet
environment, 3/3 and separately 2/3 (at max_attempts=1, no retry) correct
transcriptions with confirmed strong real-speech amplitude (peak 0.93-0.98).

RE-VERIFIED CLOSED, 2026-07-10 (same-day follow-up session): the subprocess
fix was tested head-to-head against direct in-process transcription on the
SAME recordings -- identical output every trial (verbatim-correct text,
~1.3s subprocess vs ~0.4s direct), zero stalls anywhere across dozens of
calls. The stall bug is gone and the fix demonstrably doesn't degrade
transcription. What that head-to-head also pinned down precisely is a
separate, recognizer-level behavior that had been muddying earlier
measurements: with elevated ambient room noise (peak floor measured
0.06-0.14 that day, vs the 0.015-0.024 documented quiet floor), a short
(~1.5s) utterance inside the full 5s fixed window deterministically
returns EMPTY -- in BOTH transcription paths, so it is not the fix and not
the stall. Matrix-tested, same session: the same short phrase in a 3s
window transcribed perfectly, and a longer utterance filling the 5s window
transcribed perfectly at the same noise level. In other words, the
recognizer discards short speech surrounded by a long noisy tail; speech
that fills more of the window is robust even in a noisy room. This is a
window-proportion effect of the fixed-duration recording trade-off (see
_RECORD_SECONDS note below), documented in README's Limitations -- not an
open bug in this module.
"""

from __future__ import annotations

import multiprocessing
import os
import queue
import tempfile
import time

_RECORD_SECONDS = 5
# A real, measured bug lived in an earlier version of this module: dynamic,
# amplitude-based silence detection (stop recording once it goes quiet
# again) sounded better for UX, but proved genuinely unreliable across
# repeated real tests -- 1/5 consecutive trials succeeded, with 4/5 either
# cutting off before speech was ever detected or never detecting trailing
# silence at all and running to the max cap. The underlying amplitude math
# was verified correct in isolation (real speech measured at 0.17-0.43 peak
# vs a 0.015-0.024 ambient noise floor, a clean order of magnitude apart),
# but something about repeated AVAudioEngine start/stop cycles made the
# live behavior inconsistent in ways that weren't worth continuing to chase
# blind. Fixed-duration recording removes the whole failure class: always
# record for _RECORD_SECONDS, then transcribe the complete file -- the
# file-based transcription step itself was reliable every time it was
# tested directly. Real trade-off, stated honestly: a short command still
# waits the full window, and an utterance longer than the window gets cut
# off -- see README's Limitations section.


class VoiceUnavailableError(RuntimeError):
    """Raised if on-device speech recognition isn't available on this
    machine -- fail loudly rather than silently falling back to a network
    call, which would violate this project's whole premise."""


def _record_to_file(seconds: float = _RECORD_SECONDS) -> str:
    """Records from the default microphone to a temp WAV file for a fixed
    duration, then returns the file path; caller is responsible for
    deleting it. See this module's top-of-file note for why fixed-duration
    rather than silence-detected recording."""
    import AVFoundation
    from Foundation import NSURL

    out_path = os.path.join(tempfile.gettempdir(), f"private-agent-voice-{int(time.time() * 1000)}.wav")

    engine = AVFoundation.AVAudioEngine.alloc().init()
    input_node = engine.inputNode()
    fmt = input_node.outputFormatForBus_(0)

    audio_file, err = AVFoundation.AVAudioFile.alloc().initForWriting_settings_error_(
        NSURL.fileURLWithPath_(out_path), fmt.settings(), None
    )
    if audio_file is None:
        raise VoiceUnavailableError(f"Could not open audio file for writing: {err}")

    def _tap(buffer, when):
        audio_file.writeFromBuffer_error_(buffer, None)

    input_node.installTapOnBus_bufferSize_format_block_(0, 1024, fmt, _tap)
    engine.prepare()
    engine.startAndReturnError_(None)

    time.sleep(seconds)

    engine.stop()
    input_node.removeTapOnBus_(0)
    # Explicit close, not just letting `audio_file` fall out of scope --
    # AVAudioFile's own docs are explicit that close() is "necessary...
    # in order to achieve specific control over" when a write actually
    # finishes. Without it, Python/PyObjC's own deallocation timing isn't
    # guaranteed to happen before the very next line runs, and this was a
    # real, measured bug: the exact same recording transcribed perfectly
    # when read moments later in a fresh call, but returned empty when
    # listen() called _transcribe_file() immediately after recording in
    # the same process -- a race between the write finishing and the read
    # starting, not an audio-quality problem.
    audio_file.close()
    return out_path


def _transcribe_file(path: str) -> str:
    """Runs a single on-device SFSpeechURLRecognitionRequest against a
    finished audio file and pumps the run loop until the final result (or
    an error) actually arrives -- the fix for the callback-never-fires bug
    described in this module's docstring."""
    import Speech
    from Foundation import NSDate, NSRunLoop, NSURL

    recognizer = Speech.SFSpeechRecognizer.alloc().init()
    if recognizer is None or not recognizer.supportsOnDeviceRecognition():
        raise VoiceUnavailableError("On-device speech recognition isn't available on this machine.")

    request = Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(NSURL.fileURLWithPath_(path))
    request.setRequiresOnDeviceRecognition_(True)

    state = {"text": "", "done": False, "error": None}

    def _on_result(result, error):
        if error is not None:
            state["error"] = error
            state["done"] = True
            return
        if result is not None:
            state["text"] = str(result.bestTranscription().formattedString())
            if result.isFinal():
                state["done"] = True

    task = recognizer.recognitionTaskWithRequest_resultHandler_(request, _on_result)

    run_loop = NSRunLoop.currentRunLoop()
    deadline = time.monotonic() + 15
    while not state["done"] and time.monotonic() < deadline:
        run_loop.runMode_beforeDate_("kCFRunLoopDefaultMode", NSDate.dateWithTimeIntervalSinceNow_(0.1))

    task.cancel()

    if state["error"] is not None:
        # "No speech detected" is a real, honest, expected outcome (silence,
        # or audio too quiet) -- not every error here means something's
        # broken, so this returns empty rather than always raising.
        error_str = str(state["error"])
        if "No speech detected" in error_str:
            return ""
        raise VoiceUnavailableError(f"Speech recognition failed: {error_str}")

    return state["text"].strip()


def _transcribe_worker(path: str, result_queue: "multiprocessing.Queue[tuple[str, str]]") -> None:
    """Entry point for the spawned subprocess that does the actual
    transcription -- see this module's top-of-file note for why this needs
    to run in a real separate OS process on macOS 27, not just a separate
    call. Only ever invoked via `multiprocessing`'s "spawn" context, never
    called directly."""
    try:
        result_queue.put(("ok", _transcribe_file(path)))
    except VoiceUnavailableError as e:
        result_queue.put(("err", str(e)))


def _record_and_transcribe_once() -> str:
    path = _record_to_file()
    try:
        # macOS 27 fix (2026-07-10): transcribing in-process, right after
        # recording, reliably returns empty on this OS even with confirmed
        # real, strong recorded audio -- see this module's top-of-file note
        # for the full diagnostic trail (in-process AVAudioSession teardown
        # tried and ruled out first). Running the transcription step in a
        # freshly spawned subprocess mirrors the exact separate-process case
        # already proven to work and fixes it reliably. "spawn", not the
        # platform default (which is already "spawn" on macOS, but pinned
        # explicitly here since the whole fix depends on it) -- "fork"
        # would inherit this process's live Cocoa/AVFoundation state, which
        # is the actual thing being sidestepped.
        ctx = multiprocessing.get_context("spawn")
        result_ipc: "multiprocessing.Queue[tuple[str, str]]" = ctx.Queue()
        proc = ctx.Process(target=_transcribe_worker, args=(path, result_ipc))
        proc.start()
        try:
            kind, value = result_ipc.get(timeout=20)
        except queue.Empty:
            proc.terminate()
            proc.join(timeout=5)
            raise VoiceUnavailableError("Transcription subprocess timed out without a result.")
        proc.join(timeout=5)
        if kind == "err":
            raise VoiceUnavailableError(value)
        return value
    finally:
        # Same "don't let a capture outlive its purpose" principle as
        # lantern's camera fix -- this file is audio of the user, gets
        # deleted the moment it's been transcribed, every call, no
        # exceptions.
        if os.path.exists(path):
            os.remove(path)


def listen(max_attempts: int = 3) -> str:
    """Records from the default microphone for a fixed window
    (_RECORD_SECONDS) and returns the on-device transcription. Empty string
    means every attempt genuinely heard nothing usable -- not an error, a
    real possible outcome the caller should handle (see cli.py/menubar.py).

    Retries automatically (up to max_attempts total) ONLY on an empty
    result -- this only mitigates the silence/nothing-heard failure mode,
    not misrecognition. Real testing this session (~25+ trials, see
    README's Voice front-end section) found two distinct failure kinds:
    genuine silence (which retrying reliably fixes -- a fresh attempt
    either catches real audio or it doesn't, independent trials), and the
    recognizer confidently transcribing the wrong short word instead
    ("Sachin" for "testing", "Siri" for "draft an email") -- a non-empty
    result, so retry can't distinguish it from a correct one and stops
    immediately. Don't read max_attempts as a reliability guarantee on
    content correctness; it only guarantees you don't silently get nothing
    back from a fixable miss. Each attempt is a full independent
    record+transcribe cycle -- worst case (every attempt empty) costs
    max_attempts x _RECORD_SECONDS, a real, stated trade-off, not hidden."""
    for attempt in range(max_attempts):
        text = _record_and_transcribe_once()
        if text:
            return text
    return ""


def speak(text: str, rate: float | None = None) -> None:
    """On-device speech output -- identical pattern to lantern's speech.py.
    AVSpeechSynthesizer's isSpeaking() is a plain polled property backed by
    the audio engine's own playback state, not an async delegate callback,
    so (unlike recognition) plain time.sleep() polling here is fine --
    verified this actually speaks out loud during testing, not just that
    the call doesn't raise."""
    import AVFoundation

    synthesizer = AVFoundation.AVSpeechSynthesizer.alloc().init()
    utterance = AVFoundation.AVSpeechUtterance.speechUtteranceWithString_(text)
    if rate is not None:
        utterance.setRate_(rate)

    synthesizer.speakUtterance_(utterance)
    while synthesizer.isSpeaking():
        time.sleep(0.1)
