#!/bin/sh
set -eu

cd "$(dirname "$0")"

if [ "$(git branch --show-current)" != main ]; then
    echo "Check out main before updating." >&2
    exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Commit or stash local changes to tracked files before updating." >&2
    exit 1
fi

if [ ! -f .env ]; then
    echo "Create and configure .env before updating." >&2
    exit 1
fi

docker compose version >/dev/null
git pull --ff-only origin main
docker compose build web
docker compose up -d --pull never
docker compose ps
