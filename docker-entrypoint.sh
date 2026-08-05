#!/bin/sh
# Docker auto-creates a missing bind-mount source directory on the host as
# root, which overrides the ownership baked into the image at build time.
# Started as root, this fixes it up before dropping to the unprivileged
# 'jockie' user so a fresh `docker compose up` works without anyone having
# to manually chown ./data first.
set -e

if [ "$(id -u)" = "0" ]; then
    mkdir -p /app/data
    chown -R jockie:jockie /app/data

    # /config only exists if the optional cookies.txt bind mount is in use.
    # A bind-mounted *file* (unlike an auto-created directory) keeps the
    # host file's ownership as-is, which is almost never 'jockie' — chown it
    # too so yt-dlp can write renewed session cookies back to it. yt-dlp
    # does this automatically after every request; without write access
    # cookies silently stop refreshing (or, on a genuinely read-only mount,
    # every YouTube request fails outright). Skipped gracefully when the
    # mount isn't present, so this is a no-op for anyone not using cookies.
    if [ -e /config ]; then
        chown -R jockie:jockie /config
    fi

    exec gosu jockie "$@"
fi

exec "$@"
