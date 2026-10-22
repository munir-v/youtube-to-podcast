#!/usr/bin/env python3
"""Turn a YouTube playlist into a private podcast feed hosted on Cloudflare R2.

Each run lists the playlist, downloads audio for videos it hasn't seen before,
uploads them to R2, deletes audio older than the retention window (the episodes
stay in the feed as history), and rewrites feed.xml. launchd runs it every 10 minutes (see install.sh).
"""

import argparse
import fcntl
import hashlib
import json
import logging
import shutil
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET
from email.utils import formatdate
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.toml"
COVER_PATH = ROOT / "cover.png"
LOG_PATH = Path.home() / "Library/Logs/youtube-to-podcast.log"
KEYCHAIN_SERVICE = "youtube-to-podcast"

# Stop retrying a video that keeps failing (premiere not out yet, members-only, removed).
GIVE_UP_AFTER = 3 * 86400
ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"

log = logging.getLogger("ytpod")


def keychain(account):
    """Read a secret saved with `security add-generic-password -s youtube-to-podcast -a <account> -w`."""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(f"Missing Keychain item '{account}' (service '{KEYCHAIN_SERVICE}'). See README.md.")
    return result.stdout.strip()


class R2Storage:
    def __init__(self, cfg):
        import boto3
        from botocore.config import Config

        self.bucket = cfg["bucket"]
        self.s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{cfg['account_id']}.r2.cloudflarestorage.com",
            aws_access_key_id=keychain("r2-access-key-id"),
            aws_secret_access_key=keychain("r2-secret-access-key"),
            region_name="auto",
            # Newer boto3 sends extra checksum headers by default; only send them when required.
            config=Config(
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def put_file(self, path, key, content_type, cache_control="public, max-age=86400"):
        self.s3.upload_file(
            str(path), self.bucket, key, ExtraArgs={"ContentType": content_type, "CacheControl": cache_control}
        )

    def put_bytes(self, data, key, content_type, cache_control):
        self.s3.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, CacheControl=cache_control
        )

    def delete(self, key):
        self.s3.delete_object(Bucket=self.bucket, Key=key)


class LocalStorage:
    """Writes to a local folder instead of R2, for testing without credentials."""

    def __init__(self, root):
        self.root = root

    def _dest(self, key):
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        return dest

    def put_file(self, path, key, content_type, cache_control=None):
        shutil.copyfile(path, self._dest(key))

    def put_bytes(self, data, key, content_type, cache_control=None):
        self._dest(key).write_bytes(data)

    def delete(self, key):
        (self.root / key).unlink(missing_ok=True)


def load_state(path):
    if path.exists():
        return json.loads(path.read_text())
    return {"seen": [], "episodes": [], "failures": {}, "cover_key": None, "feed_dirty": True}


def save_state(state, path):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def list_playlist(url):
    result = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--dump-single-json", url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Couldn't read playlist: {last_line(result.stderr)}")
    return json.loads(result.stdout).get("entries") or []


def download_audio(video_id, download_dir):
    """Download a video's audio as .m4a and return yt-dlp's info dict, or None if it's live right now."""
    download_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "yt-dlp",
            "--no-playlist",
            # YouTube's own AAC track plays natively on iOS, so normally nothing is re-encoded.
            "-f", "bestaudio[ext=m4a]/bestaudio",
            "-x", "--audio-format", "m4a",
            # Recording a live stream would never finish; pick it up once it's a regular video.
            "--match-filter", "!is_live",
            "-o", str(download_dir / "%(id)s.%(ext)s"),
            "--print", "after_move:%()j",
            f"https://www.youtube.com/watch?v={video_id}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(last_line(result.stderr) or f"yt-dlp exited with {result.returncode}")
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout.strip().splitlines()[-1])


def last_line(text):
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


def jpeg_thumbnail(info):
    # Podcast apps want JPEG or PNG artwork; YouTube's default thumbnail is often WebP.
    for thumb in reversed(info.get("thumbnails") or []):
        if thumb.get("url", "").split("?")[0].endswith(".jpg"):
            return thumb["url"]
    return f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg"


def build_feed(cfg, episodes, public_base, cover_key):
    ET.register_namespace("itunes", ITUNES)

    def it(tag):
        return f"{{{ITUNES}}}{tag}"

    cover_url = f"{public_base}/{cover_key}"
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = cfg["feed_title"]
    ET.SubElement(channel, "link").text = cfg["playlist_url"]
    ET.SubElement(channel, "description").text = "Audio from videos added to my YouTube playlist."
    ET.SubElement(channel, "language").text = "en"
    # Plain RSS readers use <image>; podcast apps use <itunes:image>.
    image = ET.SubElement(channel, "image")
    ET.SubElement(image, "url").text = cover_url
    ET.SubElement(image, "title").text = cfg["feed_title"]
    ET.SubElement(image, "link").text = cfg["playlist_url"]
    ET.SubElement(channel, it("author")).text = "YouTube"
    ET.SubElement(channel, it("image"), href=cover_url)
    ET.SubElement(channel, it("explicit")).text = "false"
    # Asks podcast directories not to list this feed.
    ET.SubElement(channel, it("block")).text = "Yes"

    for ep in sorted(episodes, key=lambda e: e["added"], reverse=True):
        url = f"https://www.youtube.com/watch?v={ep['id']}"
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = ep["title"]
        ET.SubElement(item, "link").text = url
        ET.SubElement(item, "guid", isPermaLink="false").text = ep["id"]
        # Date added to the playlist, not upload date, so new additions show up as the newest episode.
        ET.SubElement(item, "pubDate").text = formatdate(ep["added"], usegmt=True)
        ET.SubElement(item, "description").text = f"{ep['channel']}\n{url}\n\n{ep['description']}"
        ET.SubElement(
            item, "enclosure", url=f"{public_base}/{ep['key']}", length=str(ep["size"]), type="audio/mp4"
        )
        ET.SubElement(item, it("author")).text = ep["channel"]
        ET.SubElement(item, it("duration")).text = str(ep["duration"])
        ET.SubElement(item, it("image"), href=ep["thumbnail"])

    ET.indent(rss)
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def run(cfg, storage, public_base, state_path, download_dir):
    state = load_state(state_path)
    prefix = cfg["feed_token"]
    now = time.time()

    # The cover's key includes a hash of the file, so editing cover.png uploads it under a
    # new URL and podcast apps fetch the new artwork instead of their cached copy.
    cover_key = f"{prefix}/cover-{hashlib.sha256(COVER_PATH.read_bytes()).hexdigest()[:8]}.png"
    if state.get("cover_key") != cover_key:
        storage.put_file(COVER_PATH, cover_key, "image/png")
        # Older states only recorded that cover.png had been uploaded.
        storage.delete(state.pop("cover_key", None) or f"{prefix}/cover.png")
        state.pop("cover_uploaded", None)
        state["cover_key"] = cover_key
        state["feed_dirty"] = True
        log.info("Uploaded cover %s", cover_key)

    for entry in list_playlist(cfg["playlist_url"]):
        vid = entry["id"]
        if vid in state["seen"] or entry.get("title") in ("[Private video]", "[Deleted video]"):
            continue

        log.info("Downloading %s: %s", vid, entry.get("title"))
        try:
            info = download_audio(vid, download_dir)
            if info is None:
                raise RuntimeError("live right now")
        except Exception as e:
            failure = state["failures"].setdefault(vid, {"first_failed": now})
            failure["error"] = str(e)
            if now - failure["first_failed"] > GIVE_UP_AFTER:
                log.error("Giving up on %s: %s", vid, e)
                state["seen"].append(vid)
                del state["failures"][vid]
            else:
                log.warning("Will retry %s: %s", vid, e)
            save_state(state, state_path)
            continue

        path = Path(info["filepath"])
        key = f"{prefix}/audio/{vid}.m4a"
        storage.put_file(path, key, "audio/mp4")
        state["episodes"].append(
            {
                "id": vid,
                "title": info.get("title") or vid,
                "channel": info.get("channel") or info.get("uploader") or "",
                "description": info.get("description") or "",
                "duration": int(info.get("duration") or 0),
                "thumbnail": jpeg_thumbnail(info),
                "size": path.stat().st_size,
                "key": key,
                "added": time.time(),
            }
        )
        path.unlink()
        state["seen"].append(vid)
        state["failures"].pop(vid, None)
        state["feed_dirty"] = True
        # Save after every episode so a crash mid-run doesn't download it again.
        save_state(state, state_path)
        log.info("Added %s (%.1f MB)", vid, state["episodes"][-1]["size"] / 1e6)

    # Old audio is deleted to save storage, but the episode stays in the feed as listening history.
    cutoff = now - cfg["retention_days"] * 86400
    for ep in state["episodes"]:
        if ep["added"] < cutoff and not ep.get("audio_deleted"):
            storage.delete(ep["key"])
            ep["audio_deleted"] = True
            log.info("Deleted audio for %s (older than %d days); it stays in the feed", ep["id"], cfg["retention_days"])

    if state["feed_dirty"]:
        feed = build_feed(cfg, state["episodes"], public_base, cover_key)
        storage.put_bytes(feed, f"{prefix}/feed.xml", "application/rss+xml; charset=utf-8", "public, max-age=300")
        state["feed_dirty"] = False
    save_state(state, state_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--local", type=Path, metavar="DIR", help="write to DIR instead of R2 (keeps its own state)")
    parser.add_argument("--feed-url", action="store_true", help="print the feed URL for Pocket Casts and exit")
    args = parser.parse_args()

    cfg = tomllib.loads(CONFIG_PATH.read_text())
    cfg.setdefault("feed_title", "YouTube Queue")
    cfg.setdefault("retention_days", 7)

    handlers = [logging.FileHandler(LOG_PATH)]
    if sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)

    if args.local:
        root = args.local.resolve()
        storage, public_base, state_path = LocalStorage(root), root.as_uri(), root / "state.json"
    else:
        missing = [k for k in ("account_id", "bucket", "public_base_url") if not cfg.get(k)]
        if missing:
            sys.exit(f"Fill in {', '.join(missing)} in config.toml")
        public_base = cfg["public_base_url"].rstrip("/")
        state_path = ROOT / "state.json"
        storage = None

    if args.feed_url:
        print(f"{public_base}/{cfg['feed_token']}/feed.xml")
        return

    # launchd never overlaps runs, but a manual run could collide with a scheduled one.
    lock = open(ROOT / ".ytpod.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.info("Another run is in progress; skipping")
        return

    try:
        run(cfg, storage or R2Storage(cfg), public_base, state_path, ROOT / "downloads")
    except Exception:
        log.exception("Run failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
