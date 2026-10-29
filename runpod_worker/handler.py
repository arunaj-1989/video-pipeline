"""RunPod serverless worker: storyboard scenes -> AI images + narration -> MP4s in S3/MinIO.

Input (sent by the Universal Video Maker API):
    {"episode_id", "storyboard_revision_id", "target_duration_seconds", "sbe_job_id",
     "scenes": [{"scene_number", "duration_seconds", "narration", "visual_prompt", ...}],
     "shorts": [{"title", "hook", "duration_seconds", "narration", "call_to_action"}]}

Output:
    {"artifacts": [{"kind", "variant", "storage_uri", "checksum"}, ...]}
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from media import (
    ambient_music,
    attach_audio,
    build_segment,
    caption_cues,
    concat_segments,
    loop_clip,
    mix_music,
    probe_seconds,
    run,
    write_video,
    write_vtt,
)

VOICE_MODEL = os.environ.get("PIPER_VOICE", "/models/voice/en_US-lessac-medium.onnx")
VOICE_ENGINE = os.environ.get("VOICE_ENGINE", "kokoro")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell")
VIDEO_MODEL = os.environ.get("VIDEO_MODEL", "Wan-AI/Wan2.1-I2V-14B-720P")
# The video model lives on a network volume when available, or container disk if sufficiently sized.
VIDEO_CACHE = os.environ.get(
    "VIDEO_CACHE", "/runpod-volume/video-models" if Path("/runpod-volume").is_dir() else ""
)
ANIMATE = os.environ.get("ANIMATE_SCENES", "1") != "0"
MUSIC = os.environ.get("BACKGROUND_MUSIC", "1") != "0"
VIDEO_STEPS = int(os.environ.get("VIDEO_STEPS", "30"))
CLIP_FRAMES = 81
CLIP_FPS = 24
CLIP_SIZE = {"landscape": (1280, 720), "portrait": (720, 1280)}
VIDEO_NEGATIVE = (
    "worst quality, blurry, static image, deformed, inconsistent motion, jittery, "
    "distorted, text, watermark, cartoon, CGI render, unrealistic anatomy"
)
LANDSCAPE = (1920, 1080)
PORTRAIT = (1080, 1920)
EPISODE_SECONDS = 180

_pipeline = None
_video_pipeline = None


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        import torch

        if "flux" in IMAGE_MODEL.lower():
            from diffusers import FluxPipeline

            _pipeline = FluxPipeline.from_pretrained(
                IMAGE_MODEL, torch_dtype=torch.bfloat16
            )
            _pipeline.enable_model_cpu_offload()
        else:
            from diffusers import AutoPipelineForText2Image

            _pipeline = AutoPipelineForText2Image.from_pretrained(
                IMAGE_MODEL, torch_dtype=torch.float16, variant="fp16"
            ).to("cuda")
    return _pipeline


def get_video_pipeline():
    global _video_pipeline
    if _video_pipeline is None:
        import torch

        if "wan" in VIDEO_MODEL.lower():
            from diffusers import WanImageToVideoPipeline

            _video_pipeline = WanImageToVideoPipeline.from_pretrained(
                VIDEO_MODEL,
                torch_dtype=torch.bfloat16,
                cache_dir=VIDEO_CACHE if VIDEO_CACHE else None,
            )
            _video_pipeline.enable_model_cpu_offload()
        else:
            if not VIDEO_CACHE:
                raise RuntimeError(
                    "Animated scenes need a Network Volume (mounted at /runpod-volume) to hold the "
                    "video model. Attach one to the endpoint, or set ANIMATE_SCENES=0."
                )
            from diffusers import LTXImageToVideoPipeline
            from huggingface_hub import snapshot_download

            path = snapshot_download(
                VIDEO_MODEL,
                cache_dir=VIDEO_CACHE,
                allow_patterns=[
                    "model_index.json", "scheduler/*", "tokenizer/*",
                    "text_encoder/*", "transformer/*", "vae/*",
                ],
            )  # fmt: skip
            _video_pipeline = LTXImageToVideoPipeline.from_pretrained(path, torch_dtype=torch.bfloat16)
            _video_pipeline.enable_model_cpu_offload()
            _video_pipeline.vae.enable_tiling()
    return _video_pipeline


def animate(image_path: Path, prompt: str, orientation: str, output: Path) -> None:
    """Turn a still image into a cinematic continuous video shot with the image-to-video model."""
    from PIL import Image, ImageOps

    width, height = CLIP_SIZE[orientation]
    image = ImageOps.fit(Image.open(image_path).convert("RGB"), (width, height))
    enhanced_prompt = (
        f"{prompt[:500]}. 35mm lens, cinematic lighting, photorealistic physics, "
        f"smooth natural camera motion, high detail 8k resolution."
    )
    result = get_video_pipeline()(
        image=image,
        prompt=enhanced_prompt,
        negative_prompt=VIDEO_NEGATIVE,
        width=width,
        height=height,
        num_frames=CLIP_FRAMES,
        frame_rate=CLIP_FPS,
        num_inference_steps=VIDEO_STEPS,
        guidance_scale=4.5,
    )
    frames = (frame.convert("RGB").tobytes() for frame in result.frames[0])
    write_video(frames, width, height, CLIP_FPS, output)


def scene_video(
    prompts: list[str],
    audio: Path,
    duration: float,
    orientation: str,
    work: Path,
    name: str,
    output: Path,
) -> tuple[float, Path]:
    """Build one narrated video from one or more prompts; return spoken seconds and first image."""
    size = LANDSCAPE if orientation == "landscape" else PORTRAIT
    image_size = (1024, 576) if orientation == "landscape" else (576, 1024)
    if not ANIMATE:
        image = work / f"{name}.png"
        generate_image(prompts[0], *image_size, image)
        return build_segment(image, audio, duration, *size, output), image

    parts: list[Path] = []
    first_image: Path | None = None
    share = duration / len(prompts)
    for index, prompt in enumerate(prompts):
        image = work / f"{name}_{index}.png"
        clip = work / f"{name}_{index}.clip.mp4"
        part = work / f"{name}_{index}.part.mp4"
        generate_image(prompt, *image_size, image)
        animate(image, prompt, orientation, clip)
        loop_clip(clip, share, *size, part)
        parts.append(part)
        first_image = first_image or image
    visual = work / f"{name}.visual.mp4"
    if len(parts) == 1:
        visual = parts[0]
    else:
        concat_segments(parts, visual)
    assert first_image is not None
    return attach_audio(visual, audio, duration, output), first_image


def add_music(video: Path, work: Path, name: str) -> Path:
    """Return the video with a quiet ambient music bed mixed under the narration."""
    if not MUSIC:
        return video
    duration = probe_seconds(video)
    music = work / f"{name}.music.wav"
    mixed = work / f"{name}.mixed.mp4"
    ambient_music(duration, music)
    mix_music(video, music, duration, mixed)
    return mixed


def generate_image(prompt: str, width: int, height: int, path: Path) -> None:
    image = get_pipeline()(
        prompt=prompt[:600], num_inference_steps=4, guidance_scale=0.0, width=width, height=height
    ).images[0]
    image.save(path)


def synthesize(text: str, path: Path) -> None:
    if VOICE_ENGINE == "kokoro":
        try:
            import numpy as np
            import soundfile as sf
            from kokoro import KPipeline

            pipeline = KPipeline(lang_code="a")
            generator = pipeline(text, voice="af_heart", speed=1.0)
            audio_chunks = [chunk for _, _, chunk in generator]
            if audio_chunks:
                sf.write(str(path), np.concatenate(audio_chunks), 24000)
                return
        except Exception:
            pass  # Fall back to piper if kokoro dependencies or weights are not installed

    result = subprocess.run(
        ["piper", "--model", VOICE_MODEL, "--output_file", str(path)],
        input=text,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"piper failed: {result.stderr.strip()[-300:]}")


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )


def upload(client: Any, bucket: str, key: str, path: Path, content_type: str) -> dict[str, str]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type})
    return {"storage_uri": f"s3://{bucket}/{key}", "checksum": digest}


def ensure_bucket(client: Any, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)


def number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be a positive number.")
    return float(value)


def render(job_input: dict[str, Any]) -> dict[str, Any]:
    scenes = job_input.get("scenes")
    shorts = job_input.get("shorts")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("Input has no scenes.")
    if not isinstance(shorts, list) or len(shorts) != 3:
        raise ValueError("Input must contain exactly 3 shorts.")
    total = sum(number(s.get("duration_seconds"), "scene duration") for s in scenes)
    if abs(total - EPISODE_SECONDS) > 1:
        raise ValueError(f"Scene durations total {total}s, expected {EPISODE_SECONDS}s.")

    bucket = os.environ.get("S3_BUCKET", "universal-video-assets")
    prefix = f"episodes/{job_input['episode_id']}/{job_input.get('sbe_job_id', 'job')}"
    client = s3_client()
    ensure_bucket(client, bucket)

    with tempfile.TemporaryDirectory(prefix="uvm-render-") as directory:
        work = Path(directory)
        artifacts: list[dict[str, str]] = []

        def publish(kind: str, variant: str, path: Path, content_type: str) -> None:
            key = f"{prefix}/{path.name}"
            uploaded = upload(client, bucket, key, path, content_type)
            artifacts.append({"kind": kind, "variant": variant, **uploaded})

        segments: list[Path] = []
        cues: list[tuple[float, float, str]] = []
        cursor = 0.0
        first_image: Path | None = None
        for index, scene in enumerate(scenes, start=1):
            duration = float(scene["duration_seconds"])
            narration = str(scene.get("narration") or scene.get("visual_description") or "")
            audio = work / f"scene{index}.wav"
            segment = work / f"scene{index}.mp4"
            synthesize(narration, audio)
            prompt = str(scene.get("visual_prompt") or narration)
            spoken, image = scene_video(
                [prompt], audio, duration, "landscape", work, f"scene{index}", segment
            )
            cues += caption_cues(narration, cursor, spoken)
            cursor += duration
            segments.append(segment)
            first_image = first_image or image

        episode_raw = work / "episode_raw.mp4"
        concat_segments(segments, episode_raw)
        episode = add_music(episode_raw, work, "episode")
        write_vtt(work / "episode.vtt", cues)
        thumbnail = work / "thumbnail.png"
        run(
            [
                "ffmpeg", "-y", "-i", str(first_image), "-vf",
                "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
                str(thumbnail),
            ]
        )  # fmt: skip
        episode_name = work / "episode.mp4"
        episode.replace(episode_name)
        publish("episode_video", "episode", episode_name, "video/mp4")
        publish("captions", "episode", work / "episode.vtt", "text/vtt")
        publish("thumbnail", "thumbnail", thumbnail, "image/png")

        for index, short in enumerate(shorts, start=1):
            variant = f"short_{index}"
            duration = number(short.get("duration_seconds"), f"{variant} duration")
            text = " ".join(
                str(short.get(part) or "").strip()
                for part in ("hook", "narration", "call_to_action")
            ).strip()
            audio = work / f"{variant}.wav"
            raw = work / f"{variant}_raw.mp4"
            video = work / f"{variant}.mp4"
            vtt = work / f"{variant}.vtt"
            synthesize(text, audio)
            prompts = [
                str(short.get(part) or "").strip() for part in ("title", "hook", "narration")
            ]
            prompts = [p for p in prompts if p][:3] or [text]
            spoken, _ = scene_video(prompts, audio, duration, "portrait", work, variant, raw)
            add_music(raw, work, variant).replace(video)
            write_vtt(vtt, caption_cues(text, 0.0, spoken))
            publish("short_video", variant, video, "video/mp4")
            publish("captions", variant, vtt, "text/vtt")

    return {"artifacts": artifacts}


def handler(event: dict[str, Any]) -> dict[str, Any]:
    job_input = event.get("input") or {}
    if job_input.get("ping"):
        return {"ok": True}
    try:
        return render(job_input)
    except Exception as error:  # reported to RunPod as a failed job
        return {"error": f"{type(error).__name__}: {error}"}


if __name__ == "__main__":
    import runpod

    runpod.serverless.start({"handler": handler})
