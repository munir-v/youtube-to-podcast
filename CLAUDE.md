# youtube-to-podcast

Turns an unlisted YouTube playlist into a private podcast feed for Pocket Casts.
`ytpod.py` lists the playlist with yt-dlp, downloads new videos as `.m4a` audio,
uploads them to Cloudflare R2, deletes the audio after `retention_days` (episodes stay
in the feed as listening history, just no longer downloadable), and rewrites `feed.xml`. launchd runs it every 10 minutes.

## Files

- `ytpod.py`: the whole pipeline. `--local DIR` writes to a folder instead of R2, using its own state. `--feed-url` prints the URL to add in Pocket Casts.
- `config.toml` (gitignored): playlist, R2 bucket details and the random `feed_token` path prefix. `config.example.toml` is the template.
- `state.json` (gitignored): every video ID already processed, plus the current episodes and retry info. Delete an ID from `seen` to download it again.
- `cover.png`: podcast artwork (the YouTube logo, 1400x1400). It is uploaded as `cover-<hash>.png`, so replacing the file gives it a new URL on the next run and podcast apps pick it up.
- `run.sh`: launchd entry point. It puts Homebrew's yt-dlp, ffmpeg and deno on PATH.
- `install.sh`: copies the app to `~/Library/Application Support/youtube-to-podcast` and installs the launchd job (`local.youtube-to-podcast`) there. Re-run it after changing code, config or `cover.png`. The live `state.json` lives in that folder. `./install.sh --uninstall` removes the job.

Logs go to `~/Library/Logs/youtube-to-podcast.log`. Crashes before logging starts land in `youtube-to-podcast.launchd.log`.

## Secrets

The R2 access keys live in the macOS Keychain under service `youtube-to-podcast`. Each command prompts for its value:

```
security add-generic-password -s youtube-to-podcast -a r2-access-key-id -w
security add-generic-password -s youtube-to-podcast -a r2-secret-access-key -w
```

## Notes

- yt-dlp has to run from this Mac's home connection, because YouTube blocks datacenter IPs. Keep it updated with `brew upgrade yt-dlp` when downloads start failing.
- Downloads run one at a time at `nice` 10. Audio is YouTube's native AAC track, so there is normally no re-encode.
