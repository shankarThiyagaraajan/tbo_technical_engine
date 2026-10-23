# syntax=docker/dockerfile:1.7
# tbo_technical_engine: multi-stage build, the app in a uv-managed environment, run as a non-root user.
#
# Private git dependencies (the kit, tbo-contracts): pass a read-only token as a BuildKit secret,
# --secret id=github_token,src=<file>; it is used for one step and never stored in a layer.
# Behind an intercepting proxy, --secret id=ca_bundle,src=<pem> adds its CA for the same step.
ARG BUILD_IMAGE=python:3.12.15-bookworm@sha256:5560e9ab8709f459489e5b8aa696eda8a07ef821e14bb122be62d91234bfa98b
# uv is the only Python tool (project rule): its own image, pinned by digest, supplies the binary.
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c
ARG RUN_IMAGE=python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

FROM ${UV_IMAGE} AS uv

FROM ${BUILD_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY src src
RUN --mount=type=secret,id=github_token,required=false --mount=type=secret,id=ca_bundle,required=false <<'SH'
set -eu
if [ -s /run/secrets/ca_bundle ]; then
  export SSL_CERT_FILE=/run/secrets/ca_bundle GIT_SSL_CAINFO=/run/secrets/ca_bundle
fi
if [ -s /run/secrets/github_token ]; then
  export GIT_CONFIG_COUNT=1 GIT_CONFIG_VALUE_0=https://github.com/
  GIT_CONFIG_KEY_0="url.https://x-access-token:$(cat /run/secrets/github_token)@github.com/.insteadOf"
  export GIT_CONFIG_KEY_0
fi
uv sync --frozen --no-dev --no-editable
SH

FROM ${RUN_IMAGE}
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY contract contract
COPY config config
COPY alembic.ini alembic.ini
COPY migrations migrations
# vectorbt compiles with numba cache=True; the container filesystem is read-only, so the cache goes on the /tmp tmpfs.
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NUMBA_CACHE_DIR=/tmp/numba
# All interfaces only inside the container: the stack's internal network and Caddy are the only callers.
ENV TBO_TECHNICAL_BIND_HOST=0.0.0.0
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
# Same image for every role: `alembic upgrade head`, `celery -A tbo_technical_engine.tasks worker`, `... beat`.
CMD ["python", "-m", "tbo_technical_engine.main"]
