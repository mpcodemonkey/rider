FROM python:3.12-slim

# ffmpeg does the decoding; gosu lets the entrypoint drop root privileges
# after fixing up volume ownership; curl+unzip are only needed to install
# Deno below (removed after, they don't belong in the final image); the
# rest is for building livekit's native wheels on architectures without a
# prebuilt one.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg gosu ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a real JS runtime to solve YouTube's "n" signature challenge
# for most formats - without one, extraction silently degrades to almost
# nothing playable ("Requested format is not available", "Only images are
# available"). Deno is the runtime yt-dlp uses with zero extra config (no
# --js-runtimes flag needed, unlike Node/QuickJS/Bun). Installed as a single
# static binary from GitHub releases rather than deno.land's install script,
# so this doesn't depend on a second host being reachable at build time.
# See: https://github.com/yt-dlp/yt-dlp/wiki/EJS
ARG DENO_VERSION=2.9.4
RUN arch="$(uname -m)" \
    && case "$arch" in \
         x86_64) deno_arch=x86_64-unknown-linux-gnu ;; \
         aarch64) deno_arch=aarch64-unknown-linux-gnu ;; \
         *) echo "unsupported architecture for Deno: $arch" >&2; exit 1 ;; \
       esac \
    && curl -fsSL -o /tmp/deno.zip \
         "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${deno_arch}.zip" \
    && unzip -q /tmp/deno.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/deno \
    && rm /tmp/deno.zip \
    && apt-get purge -y curl unzip \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    && deno --version

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
