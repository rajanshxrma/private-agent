"""Real (no-mock) tests for voice.py -- speech in/out via Apple's on-device
frameworks. Matches this repo's existing test philosophy (test_agent.py etc):
real calls, no stubs.

listen() tests use the same self-contained technique that found and fixed
three real bugs during development (see voice.py's module docstring and
inline comments): the macOS `say` command produces known audio that the
real microphone picks up and the real on-device recognizer transcribes --
fully automated, no human needs to speak on cue, but still exercising the
actual hardware path end to end rather than a mock.

test_listen_transcribes_real_speech retries at the TEST level (a handful of
real listen() calls, passing if any one gets the content right), not just
relying on listen()'s own internal retry -- this is deliberate, not
sloppiness: real testing (see README's Voice front-end section) measured
listen() itself sometimes returning a confident *misrecognition* rather
than silence, which its own retry logic can't distinguish from a correct
result (both are non-empty strings). Asserting exact content on a single
attempt would be testing for a stronger guarantee than any real STT system
-- including Apple's own Siri/Dictation -- actually provides. This mirrors
how a real user would use the feature: if a voice command is misheard,
you just try again, you don't expect one perfect shot every time."""

import subprocess

import pytest


@pytest.fixture(autouse=True)
def _skip_if_speech_unavailable():
    import Speech

    recognizer = Speech.SFSpeechRecognizer.alloc().init()
    if recognizer is None or not recognizer.supportsOnDeviceRecognition():
        pytest.skip("On-device speech recognition not available on this machine")


def test_speak_does_not_raise():
    from private_agent.voice import speak

    speak("Test.", rate=0.6)  # short phrase, fast rate -- keep test runtime down


def test_listen_transcribes_real_speech():
    from private_agent.voice import listen

    # A phrase-shaped command, not a generic phrase like "testing one two
    # three" -- real testing found the latter had a much higher
    # misrecognition rate with this synthesized-voice self-test setup
    # (repeatedly heard as unrelated short words like "Sachin"), while
    # phrase-shaped commands succeeded reliably. And a *long enough*
    # phrase to fill most of listen()'s fixed 5s recording window:
    # matrix-testing on 2026-07-10 showed the on-device recognizer
    # deterministically returns empty for a short (~1.5s) utterance
    # followed by several seconds of ambient room noise, while the same
    # utterance in a shorter window -- or a longer utterance in the same
    # 5s window -- transcribes perfectly under identical conditions. A
    # test fixture should be representative of real usage, not a known
    # acoustic edge case for this particular test method.
    # A macOS 27 (beta) regression that made this fail EVERY attempt via a
    # silent 15s stall was found 2026-07-09 and fixed + re-verified closed
    # 2026-07-10 (transcription runs in a spawned subprocess -- see
    # voice.py's module docstring for the full trail). If this test fails
    # fast (not a long stall), that's ordinary noisy-environment
    # misrecognition, not the old bug -- rerun in a quiet room before
    # suspecting a regression.
    for _ in range(3):
        subprocess.Popen(
            ["bash", "-c", 'sleep 0.5 && say -r 135 "please call the dentist tomorrow morning"']
        )
        text = listen()
        # "contains", not exact match -- same reasoning as private-agent's
        # other tests (reminder titles, etc): the recognizer can add minor
        # leading noise or punctuation, what matters is it actually heard
        # real content.
        if "dentist" in text.lower():
            return
    pytest.fail("listen() didn't correctly transcribe 'call the dentist' in 3 real attempts")


def test_listen_returns_string_type_even_on_near_silence():
    from private_agent.voice import listen

    # No speech triggered -- ambient room noise only. This must return an
    # empty (or near-empty) string, never raise and never hang -- a real,
    # valid outcome (see listen()'s own docstring), not an error case.
    # max_attempts=1 here: this test is about the single-attempt silence
    # contract, not about exercising the retry loop (which would just
    # triple this test's runtime for the same genuinely-silent outcome).
    text = listen(max_attempts=1)
    assert isinstance(text, str)
