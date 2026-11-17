import argparse
from fractions import Fraction
from getpass import GetPassWarning, getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from tempfile import NamedTemporaryFile, TemporaryDirectory
import warnings
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from h3_payload import build_metaso_h3_payload, _find_first, _find_video_url, _task_record


API = "https://metaso.cn/api/minimax/v2"
FAILED = {"failed", "fail", "error", "cancelled", "canceled", "rejected"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def probe(path):
    raw = subprocess.check_output([
        "ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(path)
    ], encoding="utf-8")
    streams = json.loads(raw)["streams"]
    video = next((stream for stream in streams if stream["codec_type"] == "video"), None)
    if not video:
        raise ValueError(f"缺少视频轨：{path.name}")
    return video, any(stream["codec_type"] == "audio" for stream in streams)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-nostdin", *map(str, args)], check=True)


def audio_hash(path):
    return subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c:a", "copy",
        "-f", "hash", "-hash", "sha256", "-"
    ], encoding="utf-8").strip()


def plan_path(base, value):
    path = Path(value)
    if path.is_absolute() or path.drive:
        raise ValueError("计划里的素材和输出请使用相对于计划文件的路径")
    return base / path


def save_state(path, state):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        json.dump(state, file, indent=2)
    temporary.replace(path)


def api(path, key, payload=None):
    request = Request(API + path, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
    }, data=json.dumps(payload).encode("utf-8") if payload is not None else None)
    with build_opener(NoRedirect()).open(request, timeout=90) as response:
        return json.load(response)


def download(url, output):
    if urlsplit(url).scheme != "https":
        raise ValueError("仅接受 HTTPS 视频下载地址")
    with TemporaryDirectory(dir=output.parent) as work:
        partial = Path(work) / "download.mp4"
        with urlopen(url, timeout=90) as response, partial.open("wb") as file:
            shutil.copyfileobj(response, file)
        probe(partial)
        ffmpeg("-i", partial, "-f", "null", "-")
        os.link(partial, output)


def generate(args):
    output = args.output
    state_path = output.with_suffix(".state.json")
    if output.exists():
        raise ValueError("输出已存在")
    if args.action == "resume":
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not state.get("task_id"):
            raise ValueError("没有已知任务 ID。请先查秘塔后台，避免重复扣费")
    else:
        if state_path.exists() or output.with_suffix(".part").exists():
            raise ValueError("该输出已有任务记录或下载残片。请查状态或用 resume")
        video, has_audio = probe(args.video)
        seconds = float(video["duration"])
        if not 2 <= seconds <= 15 or has_audio:
            raise ValueError("学员版参考视频须为 2–15 秒的静音片")
        prompt = args.prompt.read_text(encoding="utf-8-sig").strip()
        if not prompt:
            raise ValueError("提示词为空")
        if args.image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("参考图使用 PNG、JPEG 或 WebP")
        probe(args.image)
        payload = build_metaso_h3_payload(
            prompt=prompt, reference_image_paths=[str(args.image)],
            reference_video_path=str(args.video), duration=args.duration, ratio="9:16",
        )
        print(json.dumps({
            "provider": "Metaso MiniMax-H3", "video_seconds": round(seconds, 3),
            "output_seconds": args.duration, "resolution": "768P", "ratio": "9:16",
            "pricing": "提交前核对客户账号当前报价",
            "network_enabled": args.action == "submit",
        }, ensure_ascii=False), flush=True)
        if args.action == "preview":
            return
        if not args.authorized and input("确认授权上传这条静音视频、一张参考图和提示词，并提交一次付费任务？输入 SUBMIT：").strip() != "SUBMIT":
            raise ValueError("已取消提交")
        state = {"status": "reserved"}
    key = os.getenv("METASO_MINIMAX_API_KEY", "").strip()
    if not key:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            try:
                key = getpass("输入你自己的秘塔 H3 API Key：").strip()
            except GetPassWarning:
                raise ValueError("此终端无法隐藏输入，请在自己的交互终端运行") from None
    if not key:
        raise ValueError("未提供 API Key")
    if args.action == "submit":
        output.parent.mkdir(parents=True, exist_ok=True)
        with state_path.open("x", encoding="utf-8") as file:
            json.dump(state, file)
        response = api("/video_generation", key, payload)
        task_id = _find_first(response, ("task_id", "video_id", "id"))
        url = _find_video_url(response)
        if not task_id and not url:
            raise ValueError("响应缺少任务 ID 和视频地址，请查秘塔后台")
        state = {"status": "submitted", "task_id": task_id}
        save_state(state_path, state)
    else:
        task_id = state["task_id"]
        url = ""
    print(f"H3 任务 ID：{task_id or '接口未返回'}", flush=True)
    deadline = time.monotonic() + 900
    while not url and time.monotonic() < deadline:
        response = api("/query/video_generation?" + urlencode({"task_id": task_id}), key)
        record = _task_record(response, task_id)
        status = _find_first(record, ("status", "state", "task_status")).lower()
        if status in FAILED:
            save_state(state_path, {"status": status, "task_id": task_id})
            raise ValueError(f"H3 任务状态：{status}")
        url = _find_video_url(record)
        if not url:
            time.sleep(10)
    if not url:
        raise ValueError("等待超时，使用 resume 查询已有任务")
    download(url, output)
    save_state(state_path, {"status": "downloaded", "task_id": task_id})
    print(f"已下载并完整解码：{output}")


def assemble(args):
    base = args.plan.parent
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    source = plan_path(base, plan["source"])
    original, has_audio = probe(source)
    fps = Fraction(original["avg_frame_rate"])
    if fps <= 0 or fps != Fraction(original["r_frame_rate"]):
        raise ValueError("请先将源片转换为固定帧率")
    total_frames = int(original["nb_read_frames"])
    source_duration = float(original["duration"])
    if abs(source_duration - float(total_frames / fps)) > 1 / fps:
        raise ValueError("原片时间线与帧数不一致，请先转固定帧率")
    shots = plan["shots"]
    if not shots:
        raise ValueError("计划缺少镜头")
    paths = list(dict.fromkeys(plan_path(base, shot["file"]).resolve() for shot in shots))
    videos = {path: probe(path)[0] for path in paths}
    filters = []
    fps_expr = f"{fps.numerator}/{fps.denominator}"
    for input_index, path in enumerate(paths):
        branches = [f"s{i}" for i, shot in enumerate(shots) if plan_path(base, shot["file"]).resolve() == path]
        scale = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1"
        outputs = "".join(f"[{label}]" for label in branches)
        filters.append(f"[{input_index}:v]{scale}" + (f",split={len(branches)}" if len(branches) > 1 else "") + outputs)
    cursor = 0
    for i, shot in enumerate(shots):
        start, end = shot["source_frames"]
        a, b = shot["generated_frames"]
        video = videos[plan_path(base, shot["file"]).resolve()]
        if any(type(n) is not int for n in (start, end, a, b)):
            raise ValueError("镜头帧号必须为整数")
        if start != cursor or not start < end <= total_frames or not 0 <= a < b <= int(video["nb_read_frames"]):
            raise ValueError(f"第 {i + 1} 镜有越界、空段、缺口或重叠")
        frames = end - start
        factor = float((frames / fps) / ((b - a) / Fraction(video["avg_frame_rate"])))
        filters.append(
            f"[s{i}]trim=start_frame={a}:end_frame={b},setpts=(PTS-STARTPTS)*{factor:.12f},"
            f"fps={fps_expr},tpad=stop_mode=clone:stop_duration=0.25,trim=end_frame={frames},"
            f"setpts=N/(({fps_expr})*TB)[v{i}]"
        )
        cursor = end
    if cursor != total_frames:
        raise ValueError("镜头未覆盖原片最后一帧")
    filters.append("".join(f"[v{i}]" for i in range(len(shots))) + f"concat=n={len(shots)}:v=1:a=0[edit]")
    inputs = [item for path in paths for item in ("-i", path)]
    if plan.get("title"):
        title = plan_path(base, plan["title"])
        filters.append(f"[edit][{len(paths) + 1}:v]overlay=0:0:eof_action=repeat[final]")
    else:
        filters.append("[edit]null[final]")
    output = args.output
    if output.exists():
        raise ValueError("输出已存在")
    output.parent.mkdir(parents=True, exist_ok=True)
    title_input = ["-i", title] if plan.get("title") else []
    audio = plan.get("audio", "source")
    audio_input = []
    audio_args = []
    if audio == "source" and has_audio:
        audio_args = ["-map", f"{len(paths)}:a:0", "-c:a", "copy"]
    elif audio == "external":
        audio_file = plan_path(base, plan["audio_file"])
        audio_input = ["-i", audio_file]
        index = len(paths) + 1 + bool(title_input)
        audio_args = ["-map", f"{index}:a:0", "-c:a", "aac",
                      "-af", f"apad,atrim=duration={source_duration:.9f}"]
    elif audio not in {"source", "none"}:
        raise ValueError("audio 须为 source、external 或 none")
    with TemporaryDirectory(dir=output.parent) as work:
        temporary = Path(work) / "output.mp4"
        ffmpeg("-n", *inputs, "-i", source, *title_input, *audio_input,
               "-filter_complex_threads", 1, "-filter_complex", ";".join(filters),
               "-map", "[final]", *audio_args, "-c:v", "libx264", "-crf", 18,
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", temporary)
        ffmpeg("-i", temporary, "-f", "null", "-")
        video, output_has_audio = probe(temporary)
        if (video["width"], video["height"], int(video["nb_read_frames"])) != (720, 1280, total_frames):
            raise ValueError("输出尺寸或帧数校验失败")
        if Fraction(video["avg_frame_rate"]) != fps:
            raise ValueError("输出帧率校验失败")
        if output_has_audio != (audio == "external" or (audio == "source" and has_audio)):
            raise ValueError("成片音轨未达到计划要求")
        if audio == "source" and has_audio and audio_hash(source) != audio_hash(temporary):
            raise ValueError("原音频载荷校验失败")
        if output.exists():
            raise ValueError("输出已存在")
        os.link(temporary, output)
    print(json.dumps({"frames": total_frames, "fps": str(fps), "full_decode": True,
                      "source_audio_identical": True if audio == "source" and has_audio else None}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="轻量 H3 复刻")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("check")
    for name in ("preview", "submit"):
        cmd = commands.add_parser(name)
        for field in ("video", "image", "prompt", "output"):
            cmd.add_argument("--" + field, required=True, type=Path)
        cmd.add_argument("--duration", type=int, choices=range(4, 16), required=True)
        if name == "submit":
            cmd.add_argument("--authorized", action="store_true", help="复用已取得的本次授权")
    cmd = commands.add_parser("resume")
    cmd.add_argument("--output", required=True, type=Path)
    cmd = commands.add_parser("assemble")
    cmd.add_argument("--plan", required=True, type=Path)
    cmd.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        for tool in ("ffmpeg", "ffprobe"):
            if not shutil.which(tool):
                raise ValueError(f"请先安装 {tool} 并加入 PATH")
        if args.action == "check":
            print("Python、FFmpeg、FFprobe 可用。此检查未连接 H3")
        elif args.action == "assemble":
            assemble(args)
        else:
            if args.output.suffix.lower() != ".mp4":
                raise ValueError("输出文件扩展名须为 .mp4")
            generate(args)
    except (ValueError, KeyError, TypeError, StopIteration, EOFError) as exc:
        print(f"停止：{exc}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as exc:
        status = getattr(exc, "code", None)
        print(f"停止：{type(exc).__name__}" + (f" HTTP {status}" if status else ""), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

