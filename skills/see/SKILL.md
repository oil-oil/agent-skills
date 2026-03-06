---
name: see
description: >
  Let AI see images and videos by converting them to text via ZenMux API (google/gemini-3-flash-preview).
  Accepts local files OR URLs (image links, video links, webpages containing video).
  Supported formats: .png/.jpg/.jpeg/.webp/.gif/.mp4/.mov/.mkv/.webm and any URL.
  Use when asked to view, describe, analyze, or extract info from any image or video content.
---

# Visual Media Parser

Give AI the ability to see images and videos by converting them to text.

## Rules

- Only run `scripts/ask_media.sh`. Do not call ZenMux API directly.
- Always write output to a file, then read that file.
- If `ZENMUX_API_KEY` is missing, ask user to provide it.

## Script

```
<skill-path>/scripts/ask_media.sh
```

Replace `<skill-path>` with the actual installed skill directory path.

## Usage

### Local image

```bash
<skill-path>/scripts/ask_media.sh --task "describe this image" --image /path/to/image.png
```

### Multiple images

```bash
<skill-path>/scripts/ask_media.sh --task "compare these two screenshots" --image /path/a.png --image /path/b.png
```

### Local video

```bash
<skill-path>/scripts/ask_media.sh --task "summarize this video" --video /path/to/video.mp4
```

### Image or video URL

```bash
<skill-path>/scripts/ask_media.sh --task "what is in this image" "https://example.com/photo.jpg"
<skill-path>/scripts/ask_media.sh --task "describe this video" "https://example.com/clip.mp4"
```

### Webpage with embedded video (uses yt-dlp)

```bash
<skill-path>/scripts/ask_media.sh --task "summarize this video" "https://youtube.com/watch?v=xxx"
```

### Custom output path

```bash
<skill-path>/scripts/ask_media.sh --task "describe" --image photo.png -o /path/output.md
```

## Output

On success, prints `output_path=<path>` to stdout. Read that file for the analysis text.

## Options

| Flag | Description | Default |
|------|-------------|---------|
| `--task` | What to analyze | "Describe this content in detail." |
| `--image` | Image path/URL (repeatable) | |
| `--video` | Video path/URL | |
| positional args | Any file path or URL | |
| `-o` | Output file path | `.runtime/visual-media-parser/<timestamp>.md` |
| `--max-upload-mb` | Max video size before auto-compress | 45 |
| `--model` | Model override | `google/gemini-3-flash-preview` |

## Dependencies

- **python3**: required
- **ffmpeg/ffprobe**: required for video compression (install: `brew install ffmpeg`)
- **yt-dlp**: optional, for extracting video from webpages (install: `brew install yt-dlp`)

## API Key

Lookup order:
1. `ZENMUX_API_KEY` env var
2. `.env.local` in current/parent directories
3. `~/.config/visual-media-parser/api_key`

## Workflow

1. Convert user request into a `--task` sentence.
2. Run the script with the appropriate input (file path, URL, or `--image`/`--video` flags).
3. Read the output file and continue working with the parsed text.
