# youtube-to-podcast

Turn a YouTube playlist into a private podcast feed. Add a video to the playlist and, a few minutes later, its audio shows up as a new episode in your podcast app.

It's a single Python script that runs on a Mac every 10 minutes. It uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) to grab the audio and hosts the files and RSS feed on [Cloudflare R2](https://developers.cloudflare.com/r2/), which has a free tier and no egress fees. I use it with Pocket Casts, but any podcast app that can subscribe to an RSS URL should work.

## How it works

On each run, `ytpod.py`:

1. Lists the playlist with yt-dlp.
2. Downloads audio for any video it hasn't seen yet. It keeps YouTube's native AAC track as `.m4a`, so there's normally no re-encode.
3. Uploads the audio to R2.
4. Deletes audio older than `retention_days` to save storage. Those episodes stay in the feed as listening history but can no longer be downloaded.
5. Rewrites `feed.xml` if anything changed.

Videos that fail to download (premieres, members-only, live streams) are retried on later runs, then skipped after 3 days. Live streams are picked up once they become regular videos.

Everything is stored under a random path prefix (`feed_token`), so the feed URL can't be guessed. The feed also sets `<itunes:block>` so directories don't list it. This is not real authentication, though: anyone who has the URL can read the feed and download the audio.

## Requirements

- macOS. It uses launchd for scheduling and the Keychain for credentials.
- Python 3.11 or newer.
- [Homebrew](https://brew.sh) with yt-dlp, ffmpeg and deno (yt-dlp needs a JavaScript runtime for YouTube):
  ```
  brew install yt-dlp ffmpeg deno
  ```
- A Cloudflare account with R2 enabled.
- A home internet connection. YouTube blocks most datacenter IPs, which is why this runs on your own machine and not on a server.

## Setup

### 1. Create the R2 bucket

1. In the Cloudflare dashboard, go to **R2** and create a bucket.
2. In the bucket's **Settings**, enable the **Public Development URL** (`https://pub-….r2.dev`) or connect a custom domain. Podcast apps fetch the feed and audio from this URL.
3. Under **R2 → Manage API tokens**, create a token with **Object Read & Write** permission, scoped to that bucket. Note the access key ID and secret access key.
4. Note your **Account ID** (shown on the R2 overview page).

### 2. Store the R2 keys in the Keychain

Each command prompts for the value:

```
security add-generic-password -s youtube-to-podcast -a r2-access-key-id -w
security add-generic-password -s youtube-to-podcast -a r2-secret-access-key -w
```

### 3. Configure

```
cp config.example.toml config.toml
```

Then fill in `config.toml`:

| Key | What it is |
| --- | --- |
| `playlist_url` | The playlist to follow. It must be public or unlisted. |
| `feed_title` | The podcast's name in your app. |
| `retention_days` | How long audio stays downloadable. |
| `account_id` | Your Cloudflare account ID. |
| `bucket` | The R2 bucket name. |
| `public_base_url` | The bucket's public URL from step 1. |
| `feed_token` | A random string used as the path prefix. Generate one with `python3 -c 'import secrets; print(secrets.token_urlsafe(18))'`. Changing it later breaks existing subscriptions. |

`config.toml` is gitignored.

### 4. Install the scheduled job

```
./install.sh
```

This copies the app to `~/Library/Application Support/youtube-to-podcast`, creates a virtual environment there, and installs a launchd job (`local.youtube-to-podcast`) that runs every 10 minutes and once right away. The copy is needed because macOS doesn't let launchd jobs read from `~/Documents` and similar folders.

Re-run `./install.sh` after changing the code, `config.toml` or `cover.png`.

### 5. Subscribe

Print the feed URL and add it to your podcast app (in Pocket Casts: **Profile → Settings → Add podcast by URL**, or search for the URL):

```
"$HOME/Library/Application Support/youtube-to-podcast/run.sh" --feed-url
```

## Usage

Add videos to the playlist. That's it.

- **Logs:** `~/Library/Logs/youtube-to-podcast.log`. If the job crashes before logging starts, look in `~/Library/Logs/youtube-to-podcast.launchd.log`.
- **Run now:** `launchctl kickstart gui/$(id -u)/local.youtube-to-podcast`
- **Re-download a video:** remove its ID from `seen` in `~/Library/Application Support/youtube-to-podcast/state.json`.
- **Change the artwork:** replace `cover.png` (1400x1400 or larger, square) and re-run `./install.sh`. It's uploaded under a new name so podcast apps pick it up.
- **Uninstall:** `./install.sh --uninstall` removes the launchd job. The app folder and its state are left in place; delete `~/Library/Application Support/youtube-to-podcast` to remove them too.

### Testing without R2

`--local DIR` writes the audio, cover and feed to a local folder instead of R2, with its own state file. It needs no credentials and no extra packages, only a `config.toml` with `playlist_url` and `feed_token` filled in:

```
python3 ytpod.py --local /tmp/ytpod-test
```

## Troubleshooting

When downloads start failing, update yt-dlp first. YouTube changes often and old versions break:

```
brew upgrade yt-dlp
```

## Note

This is meant for personal listening to videos you could watch anyway. Respect the creators and YouTube's terms of service.

## License

[MIT](LICENSE)
