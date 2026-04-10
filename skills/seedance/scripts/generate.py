"""Seedance video generation CLI — text/image/video to video.

Usage:
    python generate.py text2video "prompt" [--duration 5] [--resolution 720p] [--save out.mp4]
    python generate.py img2video "image_url" [--prompt "description"] [--duration 5] [--save out.mp4]
    python generate.py vid2video "video_url" [--prompt "description"] [--duration 5] [--save out.mp4]
    python generate.py status "task_id"

Supports multimodal input: up to 9 images, 3 videos, 3 audio files per request.
Use --last-frame to get the final frame for chaining clips into longer sequences.

Requires ARK_API_KEY env var (injected per-skill from config.yaml).
"""

import argparse
import io
import json
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests

ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
DEFAULT_MODEL = "doubao-seedance-2-0-260128"
DEFAULT_POLL_INTERVAL = 5
DEFAULT_TIMEOUT = 300


def _get_session() -> tuple[requests.Session, str]:
    api_key = os.environ.get("ARK_API_KEY")
    if not api_key:
        print("Error: ARK_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    model = os.environ.get("SEEDANCE_MODEL", DEFAULT_MODEL)
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })
    return session, model


def _submit_task(session: requests.Session, model: str, content: list, **kwargs) -> str:
    payload = {"model": model, "content": content, **kwargs}
    resp = session.post(ARK_BASE, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["id"]


def _poll_task(session: requests.Session, task_id: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = session.get(f"{ARK_BASE}/{task_id}", timeout=30)
        resp.raise_for_status()
        task = resp.json()
        status = task.get("status", "unknown")

        if status == "succeeded":
            return task
        if status in ("failed", "expired", "cancelled"):
            error = task.get("error", {})
            msg = error.get("message", status) if isinstance(error, dict) else str(error)
            print(f"Task {status}: {msg}", file=sys.stderr)
            sys.exit(1)

        elapsed = int(deadline - time.time())
        print(f"Status: {status} (timeout in {elapsed}s)", file=sys.stderr)
        time.sleep(DEFAULT_POLL_INTERVAL)

    print(f"Error: task {task_id} timed out after {timeout}s", file=sys.stderr)
    sys.exit(1)


def _download(session: requests.Session, url: str, path: str) -> None:
    resp = session.get(url, timeout=300, stream=True)
    resp.raise_for_status()
    with open(path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    print(f"Downloaded to {path}")


def _build_video_params(args: argparse.Namespace) -> dict:
    params: dict = {
        "resolution": args.resolution,
        "ratio": args.ratio,
        "duration": args.duration,
        "generate_audio": args.audio,
        "watermark": False,
    }
    if getattr(args, "last_frame", False):
        params["return_last_frame"] = True
    return params


def _print_result(result: dict) -> None:
    content = result.get("content", {})
    video_url = content.get("video_url", "")
    last_frame = content.get("last_frame_image_url", "")
    print(f"\nVideo URL: {video_url}")
    print(f"Duration: {result.get('duration', '?')}s | Resolution: {result.get('resolution', '?')} | FPS: {result.get('framespersecond', '?')}")
    if last_frame:
        print(f"Last frame: {last_frame}")
        print("(Use this as first frame of next clip to chain sequences)")


def cmd_text2video(args: argparse.Namespace) -> None:
    session, model = _get_session()
    content = [{"type": "text", "text": args.prompt}]
    params = _build_video_params(args)

    print("Submitting text-to-video task...")
    task_id = _submit_task(session, model, content, **params)
    print(f"Task ID: {task_id}")

    result = _poll_task(session, task_id)
    _print_result(result)

    if args.save:
        _download(session, result["content"]["video_url"], args.save)


def cmd_img2video(args: argparse.Namespace) -> None:
    session, model = _get_session()
    content: list = [{"type": "image_url", "image_url": args.image_url}]
    if args.prompt:
        content.append({"type": "text", "text": args.prompt})
    params = _build_video_params(args)

    print("Submitting image-to-video task...")
    task_id = _submit_task(session, model, content, **params)
    print(f"Task ID: {task_id}")

    result = _poll_task(session, task_id)
    _print_result(result)

    if args.save:
        _download(session, result["content"]["video_url"], args.save)


def cmd_vid2video(args: argparse.Namespace) -> None:
    session, model = _get_session()
    content: list = [{"type": "video_url", "video_url": {"url": args.video_url}}]
    if args.prompt:
        content.append({"type": "text", "text": args.prompt})
    params = _build_video_params(args)

    print("Submitting video-to-video task...")
    task_id = _submit_task(session, model, content, **params)
    print(f"Task ID: {task_id}")

    result = _poll_task(session, task_id)
    _print_result(result)

    if args.save:
        _download(session, result["content"]["video_url"], args.save)


def cmd_status(args: argparse.Namespace) -> None:
    session, _ = _get_session()
    resp = session.get(f"{ARK_BASE}/{args.task_id}", timeout=30)
    resp.raise_for_status()
    task = resp.json()
    print(json.dumps(task, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seedance 2.0 video generation")
    sub = parser.add_subparsers(dest="command", required=True)

    # Shared video options
    def _add_video_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("--duration", type=int, default=5, choices=[5, 10])
        p.add_argument("--resolution", default="720p", choices=["720p", "1080p"])
        p.add_argument("--ratio", default="16:9", choices=["16:9", "9:16", "1:1"])
        p.add_argument("--audio", action="store_true", default=True)
        p.add_argument("--no-audio", dest="audio", action="store_false")
        p.add_argument("--last-frame", action="store_true", help="Return last frame for chaining clips")
        p.add_argument("--save", help="Download video to this path")

    # text2video
    t2v = sub.add_parser("text2video", help="Generate video from text prompt")
    t2v.add_argument("prompt", help="Text description of the video")
    _add_video_opts(t2v)

    # img2video
    i2v = sub.add_parser("img2video", help="Generate video from image")
    i2v.add_argument("image_url", help="URL of the source image")
    i2v.add_argument("--prompt", default="", help="Optional text guidance")
    _add_video_opts(i2v)

    # vid2video
    v2v = sub.add_parser("vid2video", help="Generate video from existing video")
    v2v.add_argument("video_url", help="URL of the source video (2-15s, max 50MB)")
    v2v.add_argument("--prompt", default="", help="Text guidance for transformation")
    _add_video_opts(v2v)

    # status
    st = sub.add_parser("status", help="Check task status")
    st.add_argument("task_id", help="Task ID to check")

    args = parser.parse_args()
    commands = {
        "text2video": cmd_text2video,
        "img2video": cmd_img2video,
        "vid2video": cmd_vid2video,
        "status": cmd_status,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
