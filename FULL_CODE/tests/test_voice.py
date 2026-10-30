"""Offline voice input: recordings are checked strictly (WAV directly; the browser's WebM, Ogg or MP4 decoded on the
server), the /api/transcribe route has the same protections as chat, and a recording's text is only ever text (the
assistant answers it like typed text). A fake transcriber stands in for the speech model, so these run without the
optional extra; the decoding tests run only when it is installed."""

import importlib.util
import io
import json
import math
import threading
import unittest
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer

from api.server import make_handler
from api.state import State
from api.validation import RequestError, read_audio_body
from groundtruth.builder import build_assistant
from groundtruth.models.speech import PROMPT_CHARS, AudioError, decode_recording, read_wav, shopping_prompt
from groundtruth.paths import ROOT
from groundtruth.text import VOICE_WORDS


def wav(seconds=1.0, rate=16_000, channels=1, width=2):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(seconds * rate) * channels * width)
    return out.getvalue()


def browser_recording(container, codec, rate, seconds=2.0):
    """A recording like a browser's (WebM/Ogg with Opus, MP4 with AAC): a quiet tone, encoded with PyAV."""
    import av
    import numpy as np

    out = io.BytesIO()
    with av.open(out, mode="w", format=container) as box:
        stream = box.add_stream(codec, rate=rate)
        stream.layout = "mono"
        t = np.arange(int(rate * seconds)) / rate
        pcm = (0.3 * np.sin(2 * math.pi * 440 * t) * 32767).astype(np.int16).reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(pcm, format="s16", layout="mono")
        frame.sample_rate = rate
        for packet in [*stream.encode(frame), *stream.encode(None)]:
            box.mux(packet)
    return out.getvalue()


class FakeTranscriber:
    """Checks WAV audio like the real one (other formats need the optional extra), then returns a fixed sentence."""

    def __init__(self, text="cotton kurta under 1500"):
        self.text, self.calls = text, 0

    def transcribe(self, audio, content_type="audio/wav"):
        if content_type.startswith("audio/wav"):
            read_wav(audio, 15)
        self.calls += 1
        return self.text


class AudioTests(unittest.TestCase):
    def test_a_short_16khz_mono_recording_is_accepted(self):
        self.assertEqual(len(read_wav(wav(2), 15)), 2 * 16_000 * 2)

    def test_anything_else_is_refused(self):
        for bad in (wav(rate=44_100), wav(channels=2), wav(width=1), wav(seconds=0), wav(seconds=16), b"not audio"):
            with self.subTest(len(bad)), self.assertRaises(AudioError):
                read_wav(bad, 15)

    def test_the_speech_model_reads_the_whole_prompt(self):
        # Whisper silently drops all but the last 223 tokens of a prompt; a longer one once lost every Hinglish word
        prompt = shopping_prompt()
        self.assertLessEqual(len(prompt), PROMPT_CHARS)
        self.assertTrue(all(w in prompt for w in VOICE_WORDS))

    def test_the_upload_must_be_declared_audio_and_within_the_size_limit(self):
        body = wav(1)
        self.assertEqual(read_audio_body("audio/wav", str(len(body)), body, 600_000), body)
        self.assertEqual(read_audio_body("audio/webm;codecs=opus", str(len(body)), body, 600_000), body)
        for content_type, length, status in (("application/json", len(body), 415), ("audio/wav", 700_000, 413)):
            with self.subTest(status), self.assertRaises(RequestError) as caught:
                read_audio_body(content_type, str(length), body, 600_000)
            self.assertEqual(caught.exception.status, status)


@unittest.skipUnless(importlib.util.find_spec("av"), "needs the optional voice extra")
class BrowserRecordingTests(unittest.TestCase):
    def test_what_browsers_record_is_decoded_to_16khz(self):
        # Chrome and Firefox record WebM or Ogg (Opus), Safari MP4 (AAC); all become 16 kHz mono 16-bit samples
        for container, codec, rate, media_type in (
            ("webm", "libopus", 48_000, "audio/webm;codecs=opus"),
            ("ogg", "libopus", 48_000, "audio/ogg"),
            ("mp4", "aac", 44_100, "audio/mp4"),
        ):
            with self.subTest(container):
                samples = decode_recording(browser_recording(container, codec, rate), media_type, 15)
                self.assertAlmostEqual(len(samples) / 2 / 16_000, 2.0, delta=0.1)

    def test_unreadable_or_too_long_recordings_are_refused(self):
        too_long = browser_recording("webm", "libopus", 48_000, seconds=16)
        for bad in (b"not audio", too_long):
            with self.subTest(len(bad)), self.assertRaises(AudioError):
                decode_recording(bad, "audio/webm", 15)


class TranscribeRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assistant = build_assistant(ROOT / "data" / "catalogue.jsonl", ROOT / "data" / "policy.txt")
        page = (ROOT / "web" / "index.html").read_bytes()
        cls.fake = FakeTranscriber()
        cls.servers = {}
        for name, transcriber in (("on", cls.fake), ("off", None)):
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), make_handler(State(assistant), page, {"products": 1}, transcriber=transcriber)
            )
            threading.Thread(target=server.serve_forever, daemon=True).start()
            cls.servers[name] = server

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers.values():
            server.shutdown()
            server.server_close()

    def post(self, body, content_type="audio/wav", origin=None, server="on"):
        base = f"http://127.0.0.1:{self.servers[server].server_address[1]}"
        headers = {"Content-Type": content_type, **({"Origin": origin} if origin else {})}
        req = urllib.request.Request(base + "/api/transcribe", body, headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_a_recording_comes_back_as_text(self):
        self.assertEqual(self.post(wav(1)), (200, {"text": "cotton kurta under 1500"}))
        self.assertEqual(self.post(b"webm bytes", content_type="audio/webm")[0], 200)  # the browser's own format

    def test_bad_recordings_and_bad_requests_are_refused(self):
        self.assertEqual(self.post(wav(1, rate=8_000))[0], 400)  # not 16 kHz
        self.assertEqual(self.post(wav(1), content_type="application/json")[0], 415)
        self.assertEqual(self.post(wav(1), origin="http://evil.example")[0], 403)  # other sites can't use it
        self.assertEqual(self.post(b"\x00" * 700_000)[0], 413)  # bigger than the configured limit

    def test_without_the_speech_model_voice_is_unavailable(self):
        self.assertEqual(self.post(wav(1), server="off")[0], 503)

    def test_the_page_may_use_the_microphone_itself(self):
        base = f"http://127.0.0.1:{self.servers['on'].server_address[1]}"
        with urllib.request.urlopen(base + "/", timeout=5) as r:
            self.assertIn("microphone=(self)", r.headers["Permissions-Policy"])


if __name__ == "__main__":
    unittest.main()
