"""Voice -> text with Muse Voice Transcribe (Meta Model API).

The endpoint takes mono 16-bit PCM WAV, so browser audio (webm/ogg/m4a) is converted with a bundled
ffmpeg (imageio-ffmpeg) first.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import uuid
from pathlib import Path

import requests

import config

from . import engram


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def to_wav(audio: bytes, suffix: str = ".webm") -> bytes:
    """Convert any audio to mono 16-bit PCM WAV at 24 kHz."""
    if audio[:4] == b"RIFF":
        return audio
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"in{suffix or '.webm'}"
        dst = Path(tmp) / "out.wav"
        src.write_bytes(audio)
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "24000",
                        "-c:a", "pcm_s16le", "-map_metadata", "-1", str(dst)], check=True)
        return dst.read_bytes()


def _keywords() -> list[str]:
    """Bias recognition toward family names and places (e.g. Bluebell, Loon Lake)."""
    words = [p["name"].split()[0] for p in engram.people()] + engram.place_names()
    words += [engram.persona().get("preferred_name", "")]
    return [w for w in dict.fromkeys(words) if w][:40]


def transcribe(audio: bytes, filename: str = "audio.webm", language_bias: list[str] | None = None) -> str:
    """Return the transcript text for one recording (max 10 minutes)."""
    wav = to_wav(audio, Path(filename).suffix)
    settings = {"mode": "PUSH_TO_TALK", "model": config.MUSE_TRANSCRIBE_MODEL, "audioEncoding": "WAV",
                "keywords": _keywords()}
    if language_bias:
        settings["languageBias"] = language_bias
    r = requests.post(
        config.META_ASR_URL,
        params={"sessionId": f"magpie-{uuid.uuid4()}"},
        headers={"Authorization": f"Bearer {config.meta_api_key()}"},
        files={"request": (None, json.dumps(settings), "application/json"),
               "audio": ("audio.wav", wav, "audio/wav")},
        timeout=300,
    )
    r.raise_for_status()
    return (r.json().get("transcript") or "").strip()
