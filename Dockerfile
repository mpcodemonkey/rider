FROM python:3.12-slim

# ffmpeg does the decoding; the rest is for building livekit's native wheels
# on architectures without a prebuilt one.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy metadata first so dependency layers cache across code changes.
COPY pyproject.toml README.md ./
COPY jockiefluxer ./jockiefluxer

RUN pip install --no-cache-dir .

# Saved playlists and per-guild settings live here; mount it as a volume.
RUN mkdir -p /app/data
ENV DATABASE_PATH=/app/data/jockiefluxer.db \
    PYTHONUNBUFFERED=1

# Runs unprivileged: the bot only needs outbound network and its data volume.
RUN useradd --create-home --uid 10001 jockie && chown -R jockie:jockie /app
USER jockie

ENTRYPOINT ["python", "-m", "jockiefluxer"]
