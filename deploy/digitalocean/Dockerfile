# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.13 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 efferents \
    && useradd --uid 10001 --gid 10001 --home-dir /data/home --no-create-home efferents \
    && install -d -o 10001 -g 10001 \
        /data /data/home /data/efferents /data/submissions /data/cache /data/cache/matplotlib

WORKDIR /opt/efferents
# The root .dockerignore allows only framework code, package metadata, and
# this one offline example. Private labs, credentials, and Git never enter
# the build context. Popper Probe is a separately installed dependency.
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY efferents/ ./efferents/
COPY examples/smoke-lab/ ./examples/smoke-lab/
RUN uv venv /opt/venv \
    && uv pip install --no-cache --python /opt/venv/bin/python .

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/data/home \
    XDG_CACHE_HOME=/data/cache \
    MPLCONFIGDIR=/data/cache/matplotlib \
    EFFERENTS_HOME=/data/efferents \
    POPPER_PROBE_REPO=/data/popper-probe

USER 10001:10001
WORKDIR /data
CMD ["efferents", "serve", "--no-open", "--port", "8800"]
