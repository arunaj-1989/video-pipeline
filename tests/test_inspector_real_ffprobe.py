import shutil
import subprocess
from pathlib import Path

import pytest

from universal_video_maker.rendering import LocalFileArtifactInspector

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required",
)


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def test_inspector_reads_real_mp4_and_jpeg_metadata(tmp_path: Path) -> None:
    video = tmp_path / "episode.mp4"
    image = tmp_path / "thumbnail.jpg"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1",
        "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
        "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(video),
    )
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=160x90", "-frames:v", "1", str(image))

    inspector = LocalFileArtifactInspector()
    video_facts = inspector.inspect(video.resolve().as_uri())
    image_facts = inspector.inspect(image.resolve().as_uri())

    assert (video_facts.width, video_facts.height, video_facts.has_audio) == (320, 180, True)
    assert video_facts.content_type == "video/mp4"
    assert (image_facts.width, image_facts.height) == (160, 90)
    assert image_facts.content_type == "image/jpeg"
