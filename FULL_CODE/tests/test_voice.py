"""Offline voice input: the WAV the page sends is checked strictly, the /api/transcribe route has the same protections
as chat, and a recording's text is only ever text (the assistant answers it like typed text). A fake transcriber
stands in for the speech model, so these run without the optional extra."""

import io
import json
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
from groundtruth.models.speech import AudioError, read_wav
from groundtruth.paths import ROOT


def wav(seconds=1.0, rate=16_000, channels=1, width=2):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(seconds * rate) * channels * width)
    return out.getvalue()


class FakeTranscriber:
    """Checks the audio like the real one, then returns a fixed sentence."""

    def __init__(self, text="cotton kurta under 1500"):
        self.text, self.calls = text, 0

    def transcribe(self, audio):
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

    def test_the_upload_must_be_declared_wav_and_within_the_size_limit(self):
        body = wav(1)
        self.assertEqual(read_audio_body("audio/wav", str(len(body)), body, 600_000), body)
        for content_type, length, status in (("application/json", len(body), 415), ("audio/wav", 700_000, 413)):
            with self.subTest(status), self.assertRaises(RequestError) as caught:
                read_audio_body(content_type, str(length), body, 600_000)
            self.assertEqual(caught.exception.status, status)


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
