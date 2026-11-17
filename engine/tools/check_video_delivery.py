"""Check the delivery file and prepare a playback-tested inline preview."""

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4


FRAGMENT_LIMIT = 1_000_000
VIDEO_PAYLOAD_BUDGET = 705_000
TEMPLATE = Path(__file__).with_name("video_preview.html")


def run(command, timeout=60):
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(f"{Path(command[0]).name}：{detail[-1] if detail else result.returncode}")
    return result.stdout


def inspect_video(path):
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("交付视频文件不存在或为空")
    info = json.loads(run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path),
    ]))
    streams = info.get("streams", [])
    video = next((s for s in streams if s["codec_type"] == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = [s for s in streams if s["codec_type"] == "audio"]
    duration = float(info.get("format", {}).get("duration", 0))
    if video is None or not 0 < duration < float("inf"):
        raise ValueError("没有可用的视频流或有效时长")
    run([
        "ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(path),
        "-map", f"0:{video['index']}", "-map", "0:a?", "-sn", "-dn", "-f", "null", "-",
    ])
    return video["index"], len(audio), duration


def find_renderer():
    cache = Path.home() / ".codex/plugins/cache/openai-curated-remote/visualize"
    renderers = sorted(cache.glob("*/skills/visualize/scripts/render.py"), reverse=True)
    if not renderers:
        raise RuntimeError("本机未找到 Codex 对话内预览渲染器")
    return renderers[0]


def encode_preview(source, target, stream, audio_count, duration):
    if audio_count > 1:
        raise RuntimeError("成片有多条可切换音轨，当前内嵌播放器无法保留轨道切换")
    for width, height, frame_rate, byte_budget in [
        (384, 512, 20, VIDEO_PAYLOAD_BUDGET),
        (256, 384, 15, 560_000),
    ]:
        total_kbps = int(byte_budget * 8 / 1000 / duration)
        audio_kbps = min(32, max(12, total_kbps // 4)) if audio_count else 0
        video_kbps = min(950, total_kbps - audio_kbps - 3)
        if video_kbps < 16:
            raise RuntimeError("完整视频超过对话内预览的容量上限")
        run([
            "ffmpeg", "-v", "error", "-xerror", "-nostdin", "-y", "-i", str(source),
            "-map", f"0:{stream}", "-map", "0:a?", "-map_metadata", "-1", "-map_chapters", "-1",
            "-vf", f"scale='min({width},iw)':'min({height},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1",
            "-fpsmax", str(frame_rate), "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p",
            "-deadline", "good", "-cpu-used", "5", "-row-mt", "1", "-b:v", f"{video_kbps}k",
            "-c:a", "libopus", "-ac", "1", "-b:a", f"{audio_kbps}k", str(target),
        ])
        if 0 < target.stat().st_size <= VIDEO_PAYLOAD_BUDGET:
            return
    raise RuntimeError("完整视频压缩后仍超过对话内预览的容量上限")


def verify_playback(wrapper, duration):
    try:
        from playwright.sync_api import Error as BrowserError, sync_playwright
    except ImportError as error:
        raise RuntimeError("本机缺少后台播放测试组件") from error

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, timeout=8_000, args=["--disable-background-networking"],
            )
            try:
                page = browser.new_page(viewport={"width": 736, "height": 650})
                page.set_default_timeout(6_000)
                page.route("http*://**/*", lambda route: route.abort())
                page.goto(wrapper.as_uri(), wait_until="domcontentloaded", timeout=8_000)
                preview = page.frame_locator("#codex-visualization")
                player = preview.locator("video")
                player.wait_for(state="visible")
                frame = player.element_handle().owner_frame()
                frame.wait_for_function("""() => {
                    const video = document.querySelector('video');
                    return video && (video.readyState >= 2 || video.error);
                }""")
                initial = player.evaluate("""video => ({
                    paused: video.paused, controls: video.controls, autoplay: video.autoplay,
                    length: video.duration, width: video.videoWidth, error: video.error?.code ?? null
                })""")
                if (initial["error"] or not initial["paused"] or initial["autoplay"]
                        or not initial["controls"] or not initial["width"]
                        or initial["length"] is None or abs(initial["length"] - duration) > 0.35):
                    raise RuntimeError("预览未通过默认暂停、播放控件或完整时长检查")
                preview.get_by_role("button", name="播放视频").click()
                frame.wait_for_function(
                    "threshold => document.querySelector('video').currentTime > threshold",
                    arg=min(0.15, initial["length"] / 3),
                )
            finally:
                browser.close()
    except BrowserError as error:
        detail = str(error).splitlines()[0]
        raise RuntimeError(f"后台播放器未通过点播测试（{detail}）") from error


def prepare_preview(source, folder, stream, audio_count, duration):
    renderer = find_renderer()
    with TemporaryDirectory(prefix="video-delivery-") as scratch:
        work = Path(scratch)
        media = work / "preview.webm"
        encode_preview(source, media, stream, audio_count, duration)
        encoded = base64.b64encode(media.read_bytes()).decode("ascii")
        markup = TEMPLATE.read_text(encoding="utf-8").replace("__VIDEO_PAYLOAD__", encoded)
        fragment = markup.encode("utf-8")
        if len(fragment) >= FRAGMENT_LIMIT:
            raise RuntimeError("预览超过对话内嵌容量上限")
        preview = work / "preview.html"
        preview.write_bytes(fragment)
        wrapper = work / "playback-check.html"
        run([sys.executable, str(renderer), str(preview), str(wrapper)])
        verify_playback(wrapper, duration)

    folder.mkdir(parents=True, exist_ok=True)
    final_preview = folder / f"video-preview-{uuid4().hex[:12]}.html"
    with final_preview.open("xb") as output:
        output.write(fragment)
    return final_preview


def emit(status, **fields):
    print(json.dumps({"status": status, **fields}, ensure_ascii=False, indent=2))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--preview-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        source = args.video.resolve(strict=True)
        initial_stat = source.stat()
        stream, audio_count, duration = inspect_video(source)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        emit("file_invalid", video=str(args.video), reason=str(error))
        return 2

    checked_file = {"video": str(source), "file_link": f"[打开成片文件](<{source.as_posix()}>)"}
    try:
        preview = prepare_preview(source, args.preview_dir.resolve(), stream, audio_count, duration)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        preview = None
        preview_error = str(error)

    try:
        current = source.stat()
        if (initial_stat.st_size, initial_stat.st_mtime_ns) != (current.st_size, current.st_mtime_ns):
            raise ValueError("交付视频在检查期间被修改，请重跑检查")
    except (OSError, ValueError) as error:
        emit("file_invalid", video=str(source), reason=str(error))
        return 2

    if preview is None:
        emit("preview_unavailable", **checked_file, reason=preview_error)
    else:
        reference = json.dumps({"path": preview.as_posix(), "title": "视频预览"}, ensure_ascii=False)
        emit("preview_verified", **checked_file, preview_embed=f"visualize{reference}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
