"""Pure helpers for the render worker (no GPU, S3 or RunPod imports)."""

from __future__ import annotations

import re
import subprocess
import wave
from collections.abc import Iterable
from pathlib import Path

FPS = 25
MAX_SPEEDUP = 1.5
MAX_SLOWDOWN = 2.0
MUSIC_VOLUME = 0.25


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"{command[0]} failed: {tail}")


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / float(source.getframerate())


def timestamp(seconds: float) -> str:
    milliseconds = round(max(seconds, 0.0) * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"


def caption_cues(text: str, start: float, spoken_seconds: float) -> list[tuple[float, float, str]]:
    """Split text into sentences and spread them over the spoken time by length."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if not sentences or spoken_seconds <= 0:
        return []
    total = sum(len(s) for s in sentences)
    cues: list[tuple[float, float, str]] = []
    cursor = start
    for sentence in sentences:
        length = spoken_seconds * len(sentence) / total
        cues.append((cursor, cursor + length, sentence))
        cursor += length
    return cues


def write_vtt(path: Path, cues: list[tuple[float, float, str]]) -> None:
    lines = ["WEBVTT", ""]
    for index, (begin, end, text) in enumerate(cues, start=1):
        lines += [str(index), f"{timestamp(begin)} --> {timestamp(end)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def fit_audio(audio: Path, duration: float) -> tuple[str, float]:
    """Return an ffmpeg audio filter that fits narration into duration, and the spoken seconds."""
    spoken = wav_seconds(audio)
    if spoken > duration:
        tempo = min(spoken / duration, MAX_SPEEDUP)
        return f"atempo={tempo:.4f},apad", min(spoken / tempo, duration)
    return "apad", spoken


def build_segment(
    image: Path, audio: Path, duration: float, width: int, height: int, output: Path
) -> float:
    """Render one still image plus narration into an exact-length MP4; return spoken seconds."""
    audio_filter, spoken = fit_audio(audio, duration)
    run(
        [
            "ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(image),
            "-i", str(audio), "-t", f"{duration:.3f}",
            "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},format=yuv420p",
            "-af", audio_filter,
            "-c:v", "libx264", "-preset", "veryfast", "-r", str(FPS),
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            str(output),
        ]
    )  # fmt: skip
    return spoken


def probe_seconds(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(path),
        ],
        capture_output=True,
        text=True,
    )  # fmt: skip
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"ffprobe failed for {path.name}: {result.stderr.strip()[-200:]}")
    return float(result.stdout.strip())


def write_video(frames: Iterable[bytes], width: int, height: int, fps: int, output: Path) -> None:
    """Encode raw RGB24 frames into an H.264 MP4."""
    process = subprocess.Popen(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(output),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )  # fmt: skip
    assert process.stdin is not None and process.stderr is not None
    try:
        for frame in frames:
            process.stdin.write(frame)
    finally:
        process.stdin.close()
    error = process.stderr.read().decode("utf-8", "replace")
    if process.wait() != 0:
        raise RuntimeError(f"ffmpeg failed encoding frames: {error.strip()[-300:]}")


def loop_clip(clip: Path, duration: float, width: int, height: int, output: Path) -> None:
    """Stretch a short generated clip to an exact length by slowing it and playing it back and
    forth, which hides the loop point better than a hard cut."""
    length = probe_seconds(clip)
    slow = min(max(duration / (2 * length), 1.0), MAX_SLOWDOWN)
    cycle = output.with_suffix(".cycle.mp4")
    run(
        [
            "ffmpeg", "-y", "-i", str(clip), "-filter_complex",
            f"[0:v]setpts={slow:.4f}*PTS,fps={FPS},"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},format=yuv420p,split[a][b];"
            "[b]reverse[r];[a][r]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-r", str(FPS), str(cycle),
        ]
    )  # fmt: skip
    run(
        [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(cycle), "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", str(FPS),
            str(output),
        ]
    )  # fmt: skip


def attach_audio(video: Path, audio: Path, duration: float, output: Path) -> float:
    """Add narration to a silent video of the same length; return spoken seconds."""
    audio_filter, spoken = fit_audio(audio, duration)
    run(
        [
            "ffmpeg", "-y", "-i", str(video), "-i", str(audio), "-t", f"{duration:.3f}",
            "-af", audio_filter, "-c:v", "copy",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", str(output),
        ]
    )  # fmt: skip
    return spoken


def ambient_music(duration: float, output: Path) -> None:
    """Synthesize a soft, royalty-free ambient pad (an A minor chord with slow movement)."""
    notes = (110.0, 164.81, 220.0, 261.63, 329.63)
    command = ["ffmpeg", "-y"]
    for note in notes:
        command += [
            "-f", "lavfi", "-i", f"sine=frequency={note}:sample_rate=44100:duration={duration:.2f}"
        ]  # fmt: skip
    inputs = "".join(f"[{i}:a]" for i in range(len(notes)))
    command += [
        "-filter_complex",
        f"{inputs}amix=inputs={len(notes)}:normalize=0,tremolo=f=0.15:d=0.4,lowpass=f=900,"
        "aecho=0.8:0.7:500|900:0.35|0.25,aformat=channel_layouts=stereo[m]",
        "-map", "[m]", str(output),
    ]  # fmt: skip
    run(command)


def mix_music(
    video: Path, music: Path, duration: float, output: Path, volume: float = MUSIC_VOLUME
) -> None:
    """Mix music under the narration, ducking it while someone is speaking."""
    fade_out = max(duration - 3.0, 0.0)
    run(
        [
            "ffmpeg", "-y", "-i", str(video), "-i", str(music), "-filter_complex",
            "[0:a]asplit[voice][key];"
            f"[1:a]volume={volume},afade=t=in:d=2,afade=t=out:st={fade_out:.2f}:d=3[bed];"
            "[bed][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=500[ducked];"
            "[voice][ducked]amix=inputs=2:duration=first:normalize=0[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-ar", "44100",
            "-t", f"{duration:.3f}", "-movflags", "+faststart", str(output),
        ]
    )  # fmt: skip


def concat_segments(segments: list[Path], output: Path) -> None:
    listing = output.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in segments), encoding="utf-8")
    run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", "-movflags", "+faststart", str(output),
        ]
    )  # fmt: skip
