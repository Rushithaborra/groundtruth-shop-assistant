"""Offline speech-to-text for the web demo's mic button.

The page records the shopper with the browser's own recorder (WebM or Ogg in Chrome and Firefox, MP4 in Safari) and
posts the recording to this machine's own server, which decodes it here; nothing leaves the laptop. (Decoding in the
browser failed for some recordings, and capturing raw samples in the browser stalls when the audio output stalls.) faster-whisper (an optional extra: `uv sync --extra voice`) turns it
into text, which then goes through the assistant exactly like typed text. Without the extra or the model folder the
mic button is simply not shown.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import threading
import wave
from pathlib import Path

from ..errors import GroundTruthError
from ..text import VOICE_WORDS

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000  # what Whisper models expect


WAV_TYPES = ("audio/wav", "audio/x-wav", "audio/wave")


class AudioError(GroundTruthError, ValueError):
    """The upload is not a short recording in a format the server can read."""


def read_wav(data: bytes, max_seconds: float) -> bytes:
    """The raw 16-bit samples of a short 16 kHz mono WAV recording; AudioError for anything else."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            shape = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            frames = w.getnframes()
            samples = w.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise AudioError("The recording is not a WAV file") from exc
    if shape != (1, 2, SAMPLE_RATE):
        raise AudioError("The recording must be 16 kHz mono 16-bit audio")
    return _within_limits(samples, max_seconds)


def decode_recording(data: bytes, content_type: str, max_seconds: float) -> bytes:
    """The raw 16 kHz mono 16-bit samples of a short recording: WAV is read directly, a browser's compressed
    recording (WebM, Ogg, MP4) is decoded with PyAV, which comes with faster-whisper. AudioError for anything else."""
    if content_type.split(";")[0].strip().lower() in WAV_TYPES:
        return read_wav(data, max_seconds)
    import av  # comes with faster-whisper; its own decode_audio() breaks on PyAV 19

    try:
        with av.open(io.BytesIO(data), mode="r") as box:
            stream = next(s for s in box.streams if s.type == "audio")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            parts = [f.to_ndarray().tobytes() for frame in box.decode(stream) for f in resampler.resample(frame)]
            parts += [f.to_ndarray().tobytes() for f in resampler.resample(None)]
    except (av.FFmpegError, ValueError, StopIteration) as exc:
        raise AudioError("The recording could not be read") from exc
    return _within_limits(b"".join(parts), max_seconds)


def _within_limits(samples: bytes, max_seconds: float) -> bytes:
    """The 16-bit samples, if the recording is neither empty nor longer than allowed."""
    if not samples:
        raise AudioError("The recording is empty")
    if len(samples) > max_seconds * SAMPLE_RATE * 2:
        raise AudioError(f"Recordings can be at most {max_seconds:g} seconds")
    return samples


# Whisper keeps only the last 223 tokens of a prompt and silently drops the rest (about 3.6 characters a token)
PROMPT_CHARS = 700


def shopping_prompt() -> str:
    """What the speech model is told to expect: shoppers' Hinglish words, so "kab milega" isn't heard as "Cab will
    get it". Kept short enough for Whisper to read all of it; listing the catalogue's kinds as well made no difference
    on the spoken test questions, so they are left out."""
    return f"Shopping questions, often in Hinglish: {', '.join(VOICE_WORDS)}."


class Transcriber:
    """faster-whisper on the CPU, loaded on first use and shared; one recording is transcribed at a time."""

    def __init__(self, model_dir: str | Path, language: str, max_seconds: float, prompt: str = "") -> None:
        """Remember the settings; the model itself is loaded on the first recording, not at start-up."""
        self.model_dir, self.language, self.max_seconds, self.prompt = Path(model_dir), language, max_seconds, prompt
        self._model = None
        self._lock = threading.Lock()

    @staticmethod
    def installed(model_dir: str | Path) -> bool:
        """Whether the optional library and the model folder are both there."""
        return importlib.util.find_spec("faster_whisper") is not None and (Path(model_dir) / "model.bin").is_file()

    def transcribe(self, audio: bytes, content_type: str = "audio/wav") -> str:
        """The words in a recording, as plain text ("" when nothing was said)."""
        import numpy as np  # comes with faster-whisper

        pcm = decode_recording(audio, content_type, self.max_seconds)
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                logger.info("Loading the speech model from %s", self.model_dir)
                self._model = WhisperModel(str(self.model_dir), device="cpu", compute_type="int8")
            segments, _ = self._model.transcribe(
                samples,
                language=self.language,
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=self.prompt or None,
            )
            return " ".join(s.text.strip() for s in segments).strip()
