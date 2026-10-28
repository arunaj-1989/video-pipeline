import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runpod_worker"))

from media import caption_cues, timestamp, write_vtt  # noqa: E402


def test_timestamp_formats_webvtt() -> None:
    assert timestamp(3661.5) == "01:01:01.500"
    assert timestamp(-1) == "00:00:00.000"


def test_caption_cues_cover_spoken_time_proportionally(tmp_path: Path) -> None:
    cues = caption_cues("One two. Three four five six.", 10.0, 6.0)

    assert [c[2] for c in cues] == ["One two.", "Three four five six."]
    assert cues[0][0] == 10.0
    assert abs(cues[-1][1] - 16.0) < 1e-9

    target = tmp_path / "out.vtt"
    write_vtt(target, cues)
    assert target.read_text(encoding="utf-8").startswith("WEBVTT\n\n1\n00:00:10.000 --> ")
    assert caption_cues("", 0.0, 5.0) == []
