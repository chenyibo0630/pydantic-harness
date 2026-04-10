---
name: seedance
description: Use this skill to generate videos from text prompts and/or images. Supports text-to-video, image-to-video, and audio generation. Powered by ByteDance Seedance 2.0 via Volcengine Ark API.
---

# Seedance Skill

Generate videos using ByteDance's Seedance 2.0 model via Volcengine Ark API.

## Commands

### Text-to-Video

```bash
python {SCRIPTS_DIR}/generate.py text2video "A cat playing piano in a jazz bar, cinematic lighting" --duration 5 --resolution 720p
```

### Image-to-Video

```bash
python {SCRIPTS_DIR}/generate.py img2video "https://example.com/photo.jpg" --prompt "The person starts walking forward" --duration 5
```

### Video-to-Video

```bash
python {SCRIPTS_DIR}/generate.py vid2video "https://example.com/clip.mp4" --prompt "Change to sunset lighting" --duration 5
```

### Chain Clips (Long Video)

```bash
# Generate first clip with --last-frame
python {SCRIPTS_DIR}/generate.py text2video "A woman walking through a forest" --last-frame --save clip1.mp4
# Use the returned last_frame URL as first frame of next clip
python {SCRIPTS_DIR}/generate.py img2video "LAST_FRAME_URL" --prompt "She reaches a clearing" --last-frame --save clip2.mp4
```

### Check Task Status

```bash
python {SCRIPTS_DIR}/generate.py status "cgt-20260410-xxxx"
```

## Options

- `--duration 5` — video duration in seconds: 5 or 10 (default 5)
- `--resolution 720p` — resolution: 720p or 1080p (default 720p)
- `--ratio 16:9` — aspect ratio: 16:9, 9:16, 1:1 (default 16:9)
- `--audio` — generate audio with the video (default: enabled)
- `--no-audio` — disable audio generation
- `--last-frame` — return last frame image URL for chaining clips
- `--save PATH` — download video to local file after generation

## Input Limits

- Images: up to 9 per request (max 30MB each)
- Videos: up to 3 per request (2-15s, max 50MB each)
- Audio: up to 3 per request (MP3, max 15MB each)

## When to Use

- User asks to create/generate a video
- User provides an image and wants to animate it
- User has an existing video and wants to transform/remix it
- User wants to chain multiple clips into a longer sequence
- User describes a scene and wants a video clip

## Workflow

1. Submit generation task with text, image, and/or video
2. Poll until task completes (typically 30-120 seconds)
3. Return the video URL (valid for 24 hours)
4. Optionally download to workspace
5. For long videos: use --last-frame and chain clips

## Notes

- Video URLs expire after 24 hours — download promptly if needed
- Seedance 2.0 model ID: `doubao-seedance-2-0-260128`
- Fast model (lower quality): `doubao-seedance-2-0-fast-260128`
- Audio is generated natively in sync with video (not post-dubbed)
