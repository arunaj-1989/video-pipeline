FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/hf \
    HF_HUB_DISABLE_XET=1

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# cu128 wheels include sm_120 (Blackwell) kernels as well as older GPUs.
RUN pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cu128

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

RUN mkdir -p /models/voice \
    && curl -fL -o /models/voice/en_US-lessac-medium.onnx \
       https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx \
    && curl -fL -o /models/voice/en_US-lessac-medium.onnx.json \
       https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json

# Baked in because worker container disks are small (~6 GB) and cannot hold a runtime download.
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('stabilityai/sdxl-turbo', allow_patterns=['*.json', '*.txt', '*/*fp16.safetensors'])"

# Needed by the image-to-video pipeline's T5 tokenizer. Kept in its own late layer so the large
# model layers above stay cached when this changes.
RUN pip install sentencepiece protobuf

# Only used when the image runs as a Pod (python /app/pod_server.py); Serverless ignores it.
RUN pip install "fastapi>=0.115,<1.0" "uvicorn>=0.32,<1.0"

WORKDIR /app
COPY media.py handler.py pod_server.py /app/
CMD ["python", "-u", "/app/handler.py"]
