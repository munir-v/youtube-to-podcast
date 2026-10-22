#!/bin/zsh
# Entry point for the launchd job (see install.sh). Puts Homebrew's yt-dlp, ffmpeg and deno on PATH.
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "${0:A:h}" || exit 1
exec .venv/bin/python ytpod.py "$@"
