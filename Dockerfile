# pulse-mirror: one image for the API/dashboard, the scheduler and the CLI.
FROM python:3.13-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# dependencies first (cached layer), then the project itself
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-trixie
LABEL org.opencontainers.image.title="pulse-mirror" \
      org.opencontainers.image.description="A local mirror of your Garmin data, visualized your way" \
      org.opencontainers.image.source="https://github.com/rokemHB/pulse-mirror" \
      org.opencontainers.image.licenses="MIT"
RUN useradd --create-home --uid 1000 pulse && mkdir /data && chown pulse /data
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PULSE_DATA_DIR=/data \
    PULSE_PORT=8420 \
    TZ=Europe/Berlin
USER pulse
WORKDIR /data
VOLUME /data
EXPOSE 8420
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PULSE_PORT\"]}/healthz', timeout=4)"
CMD ["pulse-mirror", "serve"]
