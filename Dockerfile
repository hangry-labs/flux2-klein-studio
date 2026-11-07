# syntax=docker/dockerfile:1
FROM python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb AS dependencies
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
# Triton's bundled CUDA compiler needs a C compiler for its small driver launcher.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
COPY requirements-ui.lock ./requirements-ui.lock
RUN --mount=type=cache,target=/root/.cache/pip python -m pip install -r requirements-ui.lock && python -m pip check
ENV TRITON_CACHE_DIR=/data/triton \
    PYTORCH_ALLOC_CONF=expandable_segments:True
FROM dependencies AS runtime-base
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG IMAGE_VERSION=dev
ENV STUDIO_BUILD_ID=$VCS_REF STUDIO_BUILD_DATE=$BUILD_DATE STUDIO_IMAGE_VERSION=$IMAGE_VERSION
LABEL org.opencontainers.image.title="FLUX.2 Klein Studio" \
      org.opencontainers.image.source="https://github.com/hangry-labs/flux2-klein-studio" \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.created=$BUILD_DATE \
      org.opencontainers.image.version=$IMAGE_VERSION
COPY VERSION NOTICE README.md LICENSE* ./
COPY src/ ./src/
COPY assets/ ./assets/
COPY tools/probe_agent_api.py tools/probe_queue.py tools/probe_refine.py tools/probe_lora.py ./tools/
COPY THIRD_PARTY.md ./THIRD_PARTY.md
COPY licenses/ ./licenses/
COPY tools/collect_runtime_licenses.py ./tools/collect_runtime_licenses.py
RUN python tools/collect_runtime_licenses.py --output /app/licenses/runtime
ENTRYPOINT ["python", "-m", "flux2_klein_studio.container_entrypoint"]
EXPOSE 7860
CMD ["serve"]

FROM runtime-base AS source-assets
COPY tools/collect_runtime_sources.py tools/runtime_sources.toml ./tools/
COPY tools/debian-source-index.txt ./tools/debian-source-index.txt
RUN sed -i 's/Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && python tools/collect_runtime_sources.py --supplement tools/debian-source-index.txt --output /app/runtime-sources \
    && rm -rf /var/lib/apt/lists/*

FROM runtime-base AS runtime
COPY --from=source-assets /app/runtime-sources /app/runtime-sources/

FROM runtime AS checks
RUN --mount=type=cache,target=/root/.cache/pip python -m pip install httpx==0.28.1
COPY tests/ ./tests/
COPY tools/ ./tools/
RUN python -m unittest discover -s tests -v

FROM python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb AS tooling
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/matplotlib
COPY requirements-tools.txt /tmp/requirements-tools.txt
RUN --mount=type=cache,target=/root/.cache/pip python -m pip install -r /tmp/requirements-tools.txt && python -m pip check
WORKDIR /work
ENTRYPOINT ["ruff"]

FROM checks AS test

FROM dependencies AS model-assets
ENV HF_HOME=/opt/klein-models/huggingface
COPY src/flux2_klein_studio/model_catalog.py /app/src/flux2_klein_studio/model_catalog.py
COPY src/flux2_klein_studio/__init__.py /app/src/flux2_klein_studio/__init__.py
COPY tools/prefetch_models.py /app/tools/prefetch_models.py
RUN python tools/prefetch_models.py

FROM scratch AS evidence
COPY --from=runtime /app/licenses /licenses/
COPY --from=runtime /app/LICENSE /LICENSE
COPY --from=runtime /app/NOTICE /NOTICE
COPY --from=model-assets /opt/klein-models/manifest.json /model-manifest.json
COPY --from=source-assets /app/runtime-sources/manifest.json /source-manifest.json

FROM model-assets AS baked
COPY --from=runtime /app /app
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG IMAGE_VERSION=dev
ENV HF_HOME=/data/huggingface STUDIO_DEFAULT_OFFLINE=1 STUDIO_IMAGE_FLAVOR=full \
    STUDIO_BUILD_ID=$VCS_REF STUDIO_BUILD_DATE=$BUILD_DATE STUDIO_IMAGE_VERSION=$IMAGE_VERSION
LABEL org.opencontainers.image.title="FLUX.2 Klein Studio" \
      org.opencontainers.image.source="https://github.com/hangry-labs/flux2-klein-studio" \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.created=$BUILD_DATE \
      org.opencontainers.image.version=$IMAGE_VERSION
ENTRYPOINT ["python", "-m", "flux2_klein_studio.container_entrypoint"]
EXPOSE 7860
CMD ["serve"]

FROM runtime AS tiny
ENV STUDIO_DEFAULT_OFFLINE=0 STUDIO_IMAGE_FLAVOR=tiny
