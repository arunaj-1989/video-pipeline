import shutil
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runpod_worker"))

pytest.importorskip("boto3")
pytest.importorskip("PIL")
import handler  # noqa: E402
from media import probe_seconds, write_video  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required",
)


class FakeS3:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def head_bucket(self, Bucket: str) -> None:  # noqa: N803
        return None

    def upload_file(self, path: str, bucket: str, key: str, ExtraArgs: dict) -> None:  # noqa: N803
        self.files[key] = Path(path).read_bytes()


def fake_models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    from PIL import Image

    animated: list[str] = []

    def generate_image(prompt: str, width: int, height: int, path: Path) -> None:
        Image.new("RGB", (width, height), (30, 90, 160)).save(path)

    def synthesize(text: str, path: Path) -> None:
        with wave.open(str(path), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(22050)
            target.writeframes(b"\x10\x00" * 22050 * 2)

    def animate(image_path: Path, prompt: str, orientation: str, output: Path) -> None:
        animated.append(orientation)
        width, height = handler.CLIP_SIZE[orientation]
        frames = (bytes([i * 9 % 256, 60, 120]) * (width * height) for i in range(24))
        write_video(frames, width, height, 24, output)

    monkeypatch.setattr(handler, "generate_image", generate_image)
    monkeypatch.setattr(handler, "synthesize", synthesize)
    monkeypatch.setattr(handler, "animate", animate)
    monkeypatch.setattr(handler, "EPISODE_SECONDS", 8)
    return animated


def job() -> dict:
    return {
        "episode_id": "ep",
        "sbe_job_id": "job",
        "scenes": [
            {"duration_seconds": 4, "narration": "First scene.", "visual_prompt": "a hill"},
            {"duration_seconds": 4, "narration": "Second scene.", "visual_prompt": "a river"},
        ],
        "shorts": [
            {
                "title": f"Short {n}",
                "hook": "Hook.",
                "duration_seconds": 6,
                "narration": "Body.",
                "call_to_action": "Follow.",
            }
            for n in (1, 2, 3)
        ],
    }


def test_render_produces_animated_music_mixed_videos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    animated = fake_models(monkeypatch, tmp_path)
    fake = FakeS3()
    monkeypatch.setattr(handler, "s3_client", lambda: fake)
    monkeypatch.setattr(handler, "ANIMATE", True)
    monkeypatch.setattr(handler, "MUSIC", True)
    monkeypatch.setenv("S3_BUCKET", "bucket")

    result = handler.render(job())

    kinds = sorted((a["kind"], a["variant"]) for a in result["artifacts"])
    assert len(kinds) == 9
    # 2 scenes plus 3 prompts for each of the 3 shorts
    assert animated.count("landscape") == 2
    assert animated.count("portrait") == 9

    for name, seconds in (("episode.mp4", 8), ("short_1.mp4", 6)):
        key = next(k for k in fake.files if k.endswith(name))
        target = tmp_path / name
        target.write_bytes(fake.files[key])
        assert abs(probe_seconds(target) - seconds) < 0.3


def test_render_without_animation_or_music_still_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    animated = fake_models(monkeypatch, tmp_path)
    monkeypatch.setattr(handler, "s3_client", lambda: FakeS3())
    monkeypatch.setattr(handler, "ANIMATE", False)
    monkeypatch.setattr(handler, "MUSIC", False)

    result = handler.render(job())

    assert len(result["artifacts"]) == 9
    assert animated == []
