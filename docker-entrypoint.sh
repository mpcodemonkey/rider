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
    exec gosu jockie "$@"
fi

exec "$@"
