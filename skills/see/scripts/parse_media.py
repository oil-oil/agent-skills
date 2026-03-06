#!/usr/bin/env python3
"""Parse image/video files (local or URL) via ZenMux API."""

import argparse
import base64
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from urllib import error, request
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------

def _read_env_value(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if k.strip() == key:
            val = v.strip().strip("'").strip('"')
            if val:
                return val
    return ""


def resolve_api_key() -> str:
    env = os.getenv("ZENMUX_API_KEY", "").strip()
    if env:
        return env
    cur = Path.cwd().resolve()
    for d in [cur, *cur.parents]:
        found = _read_env_value(d / ".env.local", "ZENMUX_API_KEY")
        if found:
            return found
    gf = Path.home() / ".config" / "see" / "api_key"
    if gf.is_file():
        val = gf.read_text(encoding="utf-8", errors="ignore").strip()
        if val:
            return val
    return ""


# ---------------------------------------------------------------------------
# URL / download helpers
# ---------------------------------------------------------------------------

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".svg"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".m4v", ".ts"}


def _guess_media_type(url: str) -> str:
    """Return 'image', 'video', or 'unknown' based on URL path extension."""
    path = urlparse(url).path.lower()
    ext = Path(path).suffix.split("?")[0]
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return "unknown"


def _download_file(url: str, dest: Path, timeout: int = 120) -> None:
    req = request.Request(url, headers={"User-Agent": "see/1.0"})
    with request.urlopen(req, timeout=timeout) as resp:
        dest.write_bytes(resp.read())


def _try_ytdlp(url: str, dest_dir: Path) -> Path:
    """Use yt-dlp to extract a video from a webpage URL."""
    if shutil.which("yt-dlp") is None:
        raise RuntimeError(
            "yt-dlp is not installed. Install it with: brew install yt-dlp"
        )
    out_template = str(dest_dir / "downloaded.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "best[filesize<100M]/best",
        "-o", out_template,
        "--no-warnings",
        "-q",
        url,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    # find the downloaded file
    for f in dest_dir.iterdir():
        if f.name.startswith("downloaded.") and f.is_file():
            return f
    raise RuntimeError(f"yt-dlp did not produce a file for: {url}")


def resolve_input(raw: str, tmp_dir: Path) -> tuple:
    """
    Resolve a raw input string to (type, local_path).
    type is 'image' or 'video'.
    Handles: local paths, image URLs, video URLs, webpage URLs with embedded video.
    """
    # Local file
    p = Path(raw).expanduser().resolve()
    if p.is_file():
        ext = p.suffix.lower()
        if ext in IMAGE_EXTS:
            return ("image", p)
        return ("video", p)

    # URL
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError(f"Input not found as file or URL: {raw}")

    media_type = _guess_media_type(raw)

    if media_type == "image":
        dest = tmp_dir / f"download{Path(urlparse(raw).path).suffix}"
        _download_file(raw, dest)
        return ("image", dest)

    if media_type == "video":
        dest = tmp_dir / f"download{Path(urlparse(raw).path).suffix}"
        _download_file(raw, dest)
        return ("video", dest)

    # Unknown URL — try yt-dlp to extract video from webpage
    try:
        vpath = _try_ytdlp(raw, tmp_dir)
        return ("video", vpath)
    except Exception:
        # Last resort: try downloading raw and guess
        dest = tmp_dir / "download"
        _download_file(raw, dest)
        mime, _ = mimetypes.guess_type(raw)
        if mime and mime.startswith("image"):
            return ("image", dest)
        return ("video", dest)


# ---------------------------------------------------------------------------
# ZenMux API call
# ---------------------------------------------------------------------------

def call_zenmux(
    *, base_url: str, api_key: str, model: str, messages: list,
    timeout_sec: int = 600, retries: int = 3,
) -> str:
    payload = json.dumps({"model": model, "messages": messages}).encode()
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    req = request.Request(
        endpoint, data=payload, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    last_err = None
    raw = ""
    status = 0
    for attempt in range(1, retries + 1):
        try:
            with request.urlopen(req, timeout=timeout_sec) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = resp.getcode()
            break
        except error.HTTPError as e:
            last_err = RuntimeError(f"ZenMux HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}")
        except error.URLError as e:
            last_err = RuntimeError(f"ZenMux request failed: {e}")
        if attempt < retries:
            time.sleep(min(8, attempt * 2))
        elif last_err:
            raise last_err

    if status < 200 or status >= 300:
        raise RuntimeError(f"ZenMux HTTP {status}: {raw}")

    data = json.loads(raw)
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError(f"No choices in response: {raw}")
    content = choices[0].get("message", {}).get("content")
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text").strip()
    return (content or "").strip()


# ---------------------------------------------------------------------------
# File encoding
# ---------------------------------------------------------------------------

def encode_data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if not mime:
        mime = "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


# ---------------------------------------------------------------------------
# Video compression
# ---------------------------------------------------------------------------

def _video_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(r.stdout.strip())


def compress_video(video: Path, target_mb: int) -> Path:
    for cmd_name in ("ffmpeg", "ffprobe"):
        if shutil.which(cmd_name) is None:
            raise RuntimeError(f"{cmd_name} not found. Install with: brew install ffmpeg")

    duration = max(1.0, _video_duration(video))
    target_kbps = max(200, int((target_mb * 1024 * 1024 * 8 / duration) / 1000 * 0.92))
    audio_kbps = 64
    video_kbps = max(120, target_kbps - audio_kbps)

    tmp = Path(tempfile.mkdtemp(prefix="vmp-"))
    out = tmp / "compressed.mp4"

    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-vf", "scale='if(gt(iw,960),960,iw)':-2",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k",
        "-bufsize", f"{video_kbps * 2}k",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        "-movflags", "+faststart", str(out),
    ], check=True)

    # Retry tighter if still too big
    if file_size_mb(out) > target_mb:
        v2 = max(100, int(video_kbps * 0.7))
        out2 = tmp / "compressed2.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video),
            "-vf", "scale='if(gt(iw,854),854,iw)':-2",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-b:v", f"{v2}k", "-maxrate", f"{v2}k", "-bufsize", f"{v2 * 2}k",
            "-c:a", "aac", "-b:a", "48k",
            "-movflags", "+faststart", str(out2),
        ], check=True)
        out = out2

    if file_size_mb(out) > target_mb:
        raise RuntimeError(f"Video still {file_size_mb(out):.0f}MB after compression (limit {target_mb}MB)")
    return out


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------

def analyze_images(*, base_url: str, api_key: str, model: str, task: str, paths: List[Path]) -> str:
    parts: list = [{"type": "text", "text": task}]
    for p in paths:
        parts.append({"type": "image_url", "image_url": {"url": encode_data_url(p)}})
    return call_zenmux(
        base_url=base_url, api_key=api_key, model=model,
        messages=[
            {"role": "system", "content": "Analyze the provided image(s). Be thorough and accurate. Respond in the same language as the user's request."},
            {"role": "user", "content": parts},
        ],
    )


def analyze_video(*, base_url: str, api_key: str, model: str, task: str, video: Path, max_mb: int) -> str:
    upload = video
    if file_size_mb(video) > max_mb:
        print(f"Compressing video ({file_size_mb(video):.0f}MB > {max_mb}MB limit)...", file=sys.stderr)
        upload = compress_video(video, max_mb)
        print(f"Compressed to {file_size_mb(upload):.0f}MB", file=sys.stderr)
    return call_zenmux(
        base_url=base_url, api_key=api_key, model=model,
        messages=[
            {"role": "system", "content": "Analyze the provided video. Be thorough and accurate. Respond in the same language as the user's request."},
            {"role": "user", "content": [
                {"type": "text", "text": task},
                {"type": "file", "file": {"filename": upload.name, "file_data": encode_data_url(upload)}},
            ]},
        ],
        timeout_sec=900,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Parse image/video via ZenMux.")
    p.add_argument("inputs", nargs="*", help="Local file paths or URLs (images/videos/webpages).")
    p.add_argument("--image", action="append", default=[], help="Image path or URL (repeatable).")
    p.add_argument("--video", default="", help="Video path or URL.")
    p.add_argument("--task", default="Describe this content in detail.", help="What to analyze.")
    p.add_argument("-o", "--output", default="", help="Output file path.")
    p.add_argument("--max-upload-mb", type=int, default=45, help="Max video upload size in MB.")
    p.add_argument("--base-url", default=os.getenv("ZENMUX_BASE_URL", "https://zenmux.ai/api/v1"))
    p.add_argument("--model", default=os.getenv("ZENMUX_MODEL", "google/gemini-3-flash-preview"))
    return p.parse_args()


def main() -> int:
    try:
        args = parse_args()
        api_key = resolve_api_key()
        if not api_key:
            print("[ERROR] No ZENMUX_API_KEY found. Set it as env var, in .env.local, or in ~/.config/see/api_key", file=sys.stderr)
            return 1

        tmp_dir = Path(tempfile.mkdtemp(prefix="vmp-"))

        # Collect all inputs
        all_inputs = list(args.inputs)
        for img in args.image:
            all_inputs.append(img)
        if args.video:
            all_inputs.append(args.video)

        if not all_inputs:
            print("[ERROR] No input provided. Pass file paths or URLs.", file=sys.stderr)
            return 1

        # Resolve all inputs
        images: List[Path] = []
        video: Path | None = None
        for raw in all_inputs:
            mtype, path = resolve_input(raw, tmp_dir)
            if mtype == "image":
                images.append(path)
            else:
                if video is not None:
                    print("[ERROR] Only one video at a time is supported.", file=sys.stderr)
                    return 1
                video = path

        # Cannot mix images and video
        if images and video:
            print("[ERROR] Cannot mix images and video in one call. Process them separately.", file=sys.stderr)
            return 1

        # Determine output path
        if args.output:
            out_path = Path(args.output).expanduser().resolve()
        else:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            rt = Path.cwd() / ".runtime" / "see"
            rt.mkdir(parents=True, exist_ok=True)
            out_path = rt / f"{ts}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Analyze
        if images:
            result = analyze_images(
                base_url=args.base_url, api_key=api_key, model=args.model,
                task=args.task, paths=images,
            )
        else:
            result = analyze_video(
                base_url=args.base_url, api_key=api_key, model=args.model,
                task=args.task, video=video, max_mb=args.max_upload_mb,
            )

        out_path.write_text(result, encoding="utf-8")
        print(f"output_path={out_path}")
        return 0

    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
