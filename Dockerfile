FROM python:3.12-slim

# ffmpeg does the decoding; gosu lets the entrypoint drop root privileges
# after fixing up volume ownership; the rest is for building livekit's
# native wheels on architectures without a prebuilt one.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg gosu ca-certificates \
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

# The app itself runs unprivileged; only the entrypoint needs root, to fix
# up ./data's ownership when Docker bind-mounts it in as root (see
# docker-entrypoint.sh) before it drops down to this user.
RUN useradd --create-home --uid 10001 jockie && chown -R jockie:jockie /app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "jockiefluxer"]
