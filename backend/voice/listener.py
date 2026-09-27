"""
Backend voice listener — offline speech recognition on the host.

An alternative to the browser's Web Speech API: `arecord` captures 16 kHz mono
audio from a local ALSA microphone and pipes it raw into a Vosk recogniser. Each
final transcript is handed to the same `on_transcript` callback the browser path
POSTs to, so command matching and playback are identical either way.

Nothing here touches the motor bus. The recogniser's `AcceptWaveform` is the
only CPU-heavy call and it runs on the default executor so the event loop keeps
serving the 10 Hz / 50 Hz robot loops.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
# ~0.13 s of audio per read (16000 * 2 bytes * 0.13). Small enough that a spoken
# phrase is recognised promptly, large enough not to spin the loop.
READ_BYTES = 4000

# `make voice-model` unzips the model here; VOSK_MODEL_PATH overrides it.
_DEFAULT_MODEL = Path(__file__).resolve().parents[2] / "models" / "vosk-model-small-en-us-0.15"
MODEL_DIR = Path(os.environ.get("VOSK_MODEL_PATH", _DEFAULT_MODEL))

# Vosk decodes the "unknown" token when speech matches no grammar phrase — keep
# it in every grammar so stray noise resolves to "" instead of the nearest
# command.
_UNK = "[unk]"


def _grammar_json(phrases) -> str | None:
    """
    Build the JSON grammar string Vosk's KaldiRecognizer wants from raw trigger
    phrases: lowercased, punctuation stripped, de-duplicated, plus [unk].
    Returns None when there is nothing to constrain to (→ open vocabulary).
    """
    seen: list[str] = []
    for p in phrases or []:
        words = re.sub(r"[^a-z0-9 ]+", " ", str(p).lower()).split()
        norm = " ".join(words)
        if norm and norm not in seen:
            seen.append(norm)
    if not seen:
        return None
    return json.dumps(seen + [_UNK])


@dataclass
class VoiceStatus:
    running: bool = False
    model_loaded: bool = False
    device: str | None = None          # ALSA device the capture is bound to
    last_text: str = ""                 # most recent final transcript
    last_action: str | None = None      # what the transcript did (set by the handler)
    error: str | None = None
    grammar: list[str] | None = None    # phrases the recogniser is pinned to; None = open vocab


class VoiceListener:
    """Owns the arecord subprocess and the Vosk recogniser. One capture at a time."""

    def __init__(
        self,
        on_transcript: Callable[[str], Awaitable[None]],
        grammar_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self._on_transcript = on_transcript
        # Returns the phrases to pin the recogniser to (empty → open vocabulary).
        self._grammar_provider = grammar_provider or (lambda: [])
        self._model = None                       # lazily loaded; ~100 MB resident
        self._rec = None
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self.status = VoiceStatus()

    # ------------------------------------------------------------------
    # Microphone discovery
    # ------------------------------------------------------------------

    @staticmethod
    def list_mics() -> list[dict]:
        """Capture devices from `arecord -l`, as {device, label}. Empty if no ALSA."""
        if shutil.which("arecord") is None:
            return []
        try:
            out = subprocess.run(
                ["arecord", "-l"], capture_output=True, text=True, timeout=5
            ).stdout
        except Exception as exc:
            logger.warning("arecord -l failed: %s", exc)
            return []

        mics: list[dict] = []
        # e.g. "card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]"
        pat = re.compile(
            r"card (\d+): \S+ \[([^\]]+)\], device (\d+): [^\[]*\[([^\]]+)\]"
        )
        for line in out.splitlines():
            m = pat.search(line)
            if not m:
                continue
            card, card_name, dev, dev_name = m.groups()
            # plughw (not hw) so ALSA resamples/reformats to 16 kHz mono s16le
            # for mics that can't produce that natively.
            mics.append({
                "device": f"plughw:{card},{dev}",
                "label": f"{card_name} — {dev_name}",
            })
        return mics

    @staticmethod
    def probe(device: str | None = None, seconds: int = 1) -> dict:
        """
        Capture a short clip and measure its level — "is this mic actually live?".

        Returns {ok, rms, peak, dbfs, error}. `ok` is False when arecord fails or
        the capture is effectively silent (mic muted, wrong device, nothing wired).
        Not for use while the listener owns the device: a second arecord on the
        same plughw node will fail with "Device or resource busy".
        """
        if shutil.which("arecord") is None:
            return {"ok": False, "error": "arecord (ALSA tools) is not installed"}

        seconds = max(1, int(seconds))
        cmd = ["arecord", "-q", "-t", "raw", "-f", "S16_LE",
               "-r", str(SAMPLE_RATE), "-c", "1", "-d", str(seconds)]
        if device:
            cmd += ["-D", device]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=seconds + 4)
        except Exception as exc:
            return {"ok": False, "error": f"capture failed: {exc}"}

        if proc.returncode != 0 or len(proc.stdout) < 2:
            lines = proc.stderr.decode(errors="replace").strip().splitlines()
            return {"ok": False, "error": lines[-1] if lines else "no audio captured"}

        pcm = proc.stdout
        n = len(pcm) // 2
        samples = struct.unpack(f"<{n}h", pcm[: n * 2])
        peak = max((abs(s) for s in samples), default=0)
        rms = math.sqrt(sum(s * s for s in samples) / n) if n else 0.0
        dbfs = round(20 * math.log10(rms / 32768), 1) if rms > 0 else -120.0
        # A dead capture still reads a few counts of DC offset / quantisation
        # noise; anything above that floor is a real signal path.
        live = rms >= 8
        return {
            "ok": live,
            "rms": round(rms, 1),
            "peak": peak,
            "dbfs": dbfs,
            "error": None if live else "near-silence — mic muted or wrong device",
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _load_model(self):
        """Blocking: construct the Vosk model. Runs on the executor."""
        if self._model is None:
            if not MODEL_DIR.is_dir():
                raise RuntimeError(
                    f"Vosk model not found at {MODEL_DIR}. Run `make voice-model`."
                )
            try:
                from vosk import Model, SetLogLevel
            except ImportError as exc:
                raise RuntimeError(
                    "The 'vosk' package is not installed — run `make install-backend`."
                ) from exc
            SetLogLevel(-1)
            self._model = Model(str(MODEL_DIR))
            logger.info("Loaded Vosk model from %s", MODEL_DIR)
        return self._model

    def _build_recognizer(self):
        """
        A fresh KaldiRecognizer, grammar-constrained to the current command
        phrases when the provider returns any, else open-vocabulary. Blocking —
        the grammar FST is (re)built here; call it off the event loop.
        """
        from vosk import KaldiRecognizer

        model = self._load_model()
        try:
            phrases = list(self._grammar_provider() or [])
        except Exception as exc:                       # a bad registry read must not kill capture
            logger.warning("Voice grammar provider failed: %s", exc)
            phrases = []

        grammar = _grammar_json(phrases)
        if grammar is None:
            self.status.grammar = None
            return KaldiRecognizer(model, SAMPLE_RATE)
        self.status.grammar = json.loads(grammar)[:-1]  # drop the trailing [unk]
        return KaldiRecognizer(model, SAMPLE_RATE, grammar)

    def refresh_grammar(self) -> None:
        """Rebuild the recogniser against the latest command phrases, if capturing."""
        if not self.status.running:
            return
        try:
            self._rec = self._build_recognizer()
            logger.info(
                "Voice grammar refreshed (%s)",
                f"{len(self.status.grammar)} phrases" if self.status.grammar else "open vocab",
            )
        except Exception as exc:
            logger.warning("Voice grammar refresh failed: %s", exc)

    async def start(self, device: str | None = None) -> None:
        """Begin capturing from `device` (None = ALSA default). Restarts on a new device."""
        if self.status.running:
            if device == self.status.device:
                return
            await self.stop()

        if shutil.which("arecord") is None:
            raise RuntimeError("arecord (ALSA tools) is not installed on this host.")

        try:
            import vosk  # noqa: F401  — surface a clean error before we spawn arecord
        except ImportError as exc:
            raise RuntimeError(
                "The 'vosk' package is not installed — run `make install-backend`."
            ) from exc

        loop = asyncio.get_event_loop()
        self._rec = await loop.run_in_executor(None, self._build_recognizer)
        self.status.model_loaded = True

        cmd = ["arecord", "-q", "-t", "raw", "-f", "S16_LE",
               "-r", str(SAMPLE_RATE), "-c", "1"]
        if device:
            cmd += ["-D", device]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            raise RuntimeError(f"Could not start arecord: {exc}") from exc

        self.status.running = True
        self.status.device = device
        self.status.error = None
        self._task = loop.create_task(self._listen_loop())
        logger.info("Backend voice listener started (device=%s)", device or "default")

    async def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
            except Exception as exc:
                logger.warning("Error stopping arecord: %s", exc)
            self._proc = None

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        self._rec = None
        self.status.running = False
        self.status.device = None
        self.status.grammar = None
        logger.info("Backend voice listener stopped")

    # ------------------------------------------------------------------
    # Capture loop
    # ------------------------------------------------------------------

    async def _listen_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        loop = asyncio.get_event_loop()
        try:
            while True:
                data = await self._proc.stdout.read(READ_BYTES)
                if not data:
                    # arecord exited — surface the last line of its stderr.
                    err = ""
                    if self._proc.stderr is not None:
                        lines = (await self._proc.stderr.read()).decode(
                            errors="replace"
                        ).strip().splitlines()
                        err = lines[-1] if lines else ""
                    self.status.error = err or "audio capture ended unexpectedly"
                    logger.warning("arecord ended: %s", self.status.error)
                    break

                accepted = await loop.run_in_executor(
                    None, self._rec.AcceptWaveform, data
                )
                if not accepted:
                    continue
                text = json.loads(self._rec.Result()).get("text", "").strip()
                if not text:
                    continue

                self.status.last_text = text
                logger.info("Voice heard: %s", text)
                try:
                    await self._on_transcript(text)
                except Exception as exc:
                    logger.warning("Voice transcript handler error: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status.error = str(exc)
            logger.warning("Voice listen loop error: %s", exc)
        finally:
            self.status.running = False
