# syntax=docker/dockerfile:1
FROM python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/data/huggingface \
    XDG_CACHE_HOME=/data/cache \
    TORCH_HOME=/data/torch \
    DIFFUSERS_TRUST_REMOTE_KERNELS=true \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    PYTHONPATH=/app/src
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip python -m pip install -r requirements.lock && python -m pip check
COPY src/ ./src/
COPY THIRD_PARTY.md ./THIRD_PARTY.md
ENTRYPOINT ["python", "-m", "flux2_klein_studio"]
CMD ["doctor"]

FROM runtime AS test
COPY tests/ ./tests/
RUN python -m unittest discover -s tests -v
