import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runpod_worker"))

from media import (  # noqa: E402
    ambient_music,
    attach_audio,
    loop_clip,
    mix_music,
    probe_seconds,
    write_video,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required",
)


def streams(path: Path) -> list[str]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
            "-of", "csv=p=0", str(path),
        ],  # fmt: skip
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return sorted(out)


def make_clip(path: Path, width: int = 64, height: int = 48, count: int = 24) -> None:
    frames = (bytes([i * 10 % 256, 40, 200]) * (width * height) for i in range(count))
    write_video(frames, width, height, 24, path)


def make_wav(path: Path, seconds: float) -> None:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(22050)
        target.writeframes(b"\x10\x00" * int(22050 * seconds))


def test_loop_clip_stretches_a_short_clip_to_an_exact_length(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    make_clip(clip)
    assert abs(probe_seconds(clip) - 1.0) < 0.1

    stretched = tmp_path / "scene.mp4"
    loop_clip(clip, 7.0, 128, 72, stretched)

    assert abs(probe_seconds(stretched) - 7.0) < 0.15
    assert streams(stretched) == ["video"]


def test_narration_and_music_end_up_in_an_exact_length_video(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    make_clip(clip)
    video = tmp_path / "scene.mp4"
    loop_clip(clip, 6.0, 128, 72, video)
    voice = tmp_path / "voice.wav"
    make_wav(voice, 4.0)

    narrated = tmp_path / "narrated.mp4"
    spoken = attach_audio(video, voice, 6.0, narrated)
    assert abs(spoken - 4.0) < 0.01
    assert abs(probe_seconds(narrated) - 6.0) < 0.15
    assert streams(narrated) == ["audio", "video"]

    music = tmp_path / "music.wav"
    ambient_music(6.0, music)
    final = tmp_path / "final.mp4"
    mix_music(narrated, music, 6.0, final)
    assert abs(probe_seconds(final) - 6.0) < 0.15
    assert streams(final) == ["audio", "video"]
