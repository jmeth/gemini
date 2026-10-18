"""
Play sounds through the robot host's own speaker.

The Pi (or Mac) running the backend makes the noise, so a button, timer or
object trigger works with no browser open. No audio library: each platform's
stock command-line player does the job, and the first one found is used.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def _candidates(path: str, volume: float) -> list[tuple[str, list[str]]]:
    """Players worth trying, best first, as (tool, argv)."""
    suffix = Path(path).suffix.lower()
    v = max(0.0, min(1.0, volume))
    out: list[tuple[str, list[str]]] = []
    if platform.system() == "Darwin":
        out.append(("afplay", ["afplay", "-v", f"{v:.2f}", path]))
    out.append(("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                           "-volume", str(int(v * 100)), path]))
    if suffix in (".wav", ".aiff", ".aif"):
        out.append(("aplay", ["aplay", "-q", path]))
    if suffix == ".mp3":
        out.append(("mpg123", ["mpg123", "-q", "-f", str(int(v * 32768)), path]))
    out.append(("paplay", ["paplay", f"--volume={int(v * 65536)}", path]))
    return out


def available_player() -> str | None:
    """Name of the player that would be used, or None if the host has none."""
    for tool, _ in _candidates("x.wav", 1.0):
        if shutil.which(tool):
            return tool
    return None


class AudioPlayer:
    def __init__(self) -> None:
        self._procs: set[asyncio.subprocess.Process] = set()

    async def play(self, path: Path, volume: float = 1.0, wait: bool = False) -> None:
        """Start a sound. With wait=True, return when it has finished."""
        if not path.exists():
            raise RuntimeError(f"Sound file is missing: {path.name}")
        for tool, argv in _candidates(str(path), volume):
            if not shutil.which(tool):
                continue
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            self._procs.add(proc)
            if wait:
                try:
                    await proc.wait()
                except asyncio.CancelledError:
                    self._kill(proc)
                    raise
                finally:
                    self._procs.discard(proc)
            else:
                asyncio.ensure_future(self._reap(proc))
            return
        raise RuntimeError(
            "No audio player found on this host — install ffmpeg (ffplay), or on a Pi "
            "alsa-utils (aplay) / mpg123")

    async def _reap(self, proc: asyncio.subprocess.Process) -> None:
        try:
            await proc.wait()
        finally:
            self._procs.discard(proc)

    @staticmethod
    def _kill(proc: asyncio.subprocess.Process) -> None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass

    async def stop(self) -> int:
        """Stop everything currently playing. Returns how many sounds were cut off."""
        procs = list(self._procs)
        for p in procs:
            self._kill(p)
        return len(procs)
