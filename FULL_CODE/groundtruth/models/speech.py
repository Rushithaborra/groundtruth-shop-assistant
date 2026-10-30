"""Offline speech-to-text for the web demo's mic button.

The page records the shopper, converts the recording to 16 kHz mono 16-bit WAV in the browser, and posts it to this
machine's own server; nothing leaves the laptop. faster-whisper (an optional extra: `uv sync --extra voice`) turns it
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


class AudioError(GroundTruthError, ValueError):
    """The upload is not the short 16 kHz mono 16-bit WAV recording the page sends."""


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
    if frames == 0:
        raise AudioError("The recording is empty")
    if frames > max_seconds * SAMPLE_RATE:
        raise AudioError(f"Recordings can be at most {max_seconds:g} seconds")
    return samples


def shopping_prompt(kinds: list[str]) -> str:
    """What the speech model is told to expect: shoppers' Hinglish words and this catalogue's kinds of product
    ("kab milega" is otherwise heard as "Cab will get it"). Built from the catalogue, so any shop gets its own."""
    return f"Shopping questions, often in Hinglish: {', '.join(VOICE_WORDS)}. Products: {', '.join(kinds).lower()}."


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

    def transcribe(self, wav: bytes) -> str:
        """The words in a recording, as plain text ("" when nothing was said)."""
        import numpy as np  # comes with faster-whisper

        samples = np.frombuffer(read_wav(wav, self.max_seconds), dtype=np.int16).astype(np.float32) / 32768.0
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
