"""Prepare the approved remake for Lemo and render the finished local video."""

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def run(command, *, cwd=None, env=None, capture=True):
    result = subprocess.run(
        [str(part) for part in command], cwd=cwd, env=env, check=False,
        capture_output=capture, encoding="utf-8", errors="replace",
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{command[0]} 执行失败 ({result.returncode}) {detail[-2400:]}")
    return result.stdout


def paths():
    defaults = {
        "remake_project": "engine",
        "video_reverse": "engine/.agents/skills/video-reverse",
        "remake_tools": "tools/video-remake",
        "lemo_library": ".deps/lemo-opuscar",
    }
    settings = ROOT / "local-paths.json"
    overrides = read(settings) if settings.exists() else {}
    require(isinstance(overrides, dict) and set(overrides) <= {*defaults, "chromium"},
            "local-paths.json 含有未知配置项，请参考 local-paths.example.json")
    require(all(isinstance(value, str) and value.strip() for value in overrides.values()),
            "local-paths.json 中的路径须为非空字符串")
    config = {key: (ROOT / Path(value).expanduser()).resolve()
              for key, value in {**defaults, **overrides}.items()}
    remake = config["remake_project"]
    lemo = config["lemo_library"]
    components = {
        "remake_rules": remake / "AGENTS.md",
        "remake_skill": remake / ".agents/skills/tiktok-sales-video-remake/SKILL.md",
        "reverse_skill": config["video_reverse"] / "SKILL.md",
        "reverse_collector": config["video_reverse"] / "scripts/collect_evidence.py",
        "capsule_handoff": config["remake_tools"] / "tools/handoff.py",
        "remake_assembler": config["remake_tools"] / "tools/remake.py",
        "preview_tool": remake / "tools/check_video_delivery.py",
        "lemo_skill": lemo / "plugin/skills/lemo-opuscar/SKILL.md",
        "lemo_rules": lemo / "AGENTS.md",
        "lemo_readcheck": lemo / "core/render/readcheck.mjs",
        "lemo_render": lemo / "core/render/video.mjs",
    }
    chromium = os.environ.get("PLAYWRIGHT_CHROME") or config.get("chromium")
    if chromium:
        components["chromium"] = (ROOT / Path(chromium).expanduser()).resolve()
    return components


def lemo_env(components):
    env = os.environ.copy()
    if "chromium" in components:
        env["PLAYWRIGHT_CHROME"] = str(components["chromium"])
    return env


def check():
    components = paths()
    commands = {name: shutil.which(name) for name in ("ffprobe", "ffmpeg", "node")}
    missing = [str(path) for path in components.values() if not path.is_file()]
    missing.extend(name for name, path in commands.items() if path is None)
    require(not missing, "本机路径或程序缺失 " + "，".join(missing))
    for key in ("reverse_collector", "capsule_handoff", "remake_assembler", "preview_tool"):
        run([sys.executable, "-B", components[key], "--help"])
    run([
        "node", "--input-type=module", "-e",
        "import {chromium} from 'playwright-core';"
        "import {EXE, ARGS} from './core/render/browser.mjs';"
        "const browser = await chromium.launch({executablePath: EXE, args: ARGS, timeout: 10000});"
        "await browser.close();",
    ], cwd=components["lemo_rules"].parent, env=lemo_env(components))
    print(json.dumps({
        "status": "local_tools_ready", "paths": {key: str(path) for key, path in components.items()},
        "commands": commands, "h3_service": "未提交任务",
    }, ensure_ascii=False, indent=2))


def probe(file):
    data = json.loads(run([
        "ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format",
        "-of", "json", file,
    ]))
    video = next((stream for stream in data["streams"] if stream["codec_type"] == "video"
                  and not stream.get("disposition", {}).get("attached_pic")), None)
    require(video is not None, f"文件缺少实际视频轨 {file}")
    media = {
        "frames": int(video["nb_read_frames"]), "fps": video["avg_frame_rate"],
        "width": video["width"], "height": video["height"],
        "video_duration": float(video["duration"]),
        "duration": float(data["format"]["duration"]),
        "audio_tracks": sum(stream["codec_type"] == "audio" for stream in data["streams"]),
    }
    require(media["frames"] > 0 and Fraction(media["fps"]) > 0, "视频帧数或帧率无效")
    return media


def prepare(brief_file):
    working = brief_file.parent
    brief = read(brief_file)
    approval = brief["base_review"]
    require(approval["status"] == "passed" and approval["record"].strip(), "需要该版底片的通过记录")
    base = (working / brief["base"]).resolve(strict=True)
    digest = sha256(base)
    require(digest == approval["sha256"].lower(), "底片与通过记录的 SHA-256 不一致")
    evidence = read(working / brief["shot_evidence"])
    require(digest == evidence["source_sha256"], "切点证据来自另一版底片")
    media = probe(base)
    timestamps = evidence["frame_times_seconds"]
    end_time = evidence["duration_seconds"]
    require(len(timestamps) == media["frames"] == evidence["frame_count"], "帧时钟长度不符")
    require(timestamps[0] == 0 and all(a < b for a, b in zip(timestamps, timestamps[1:]))
            and timestamps[-1] < end_time, "帧时钟未连续递增")
    require(abs(media["video_duration"] - end_time) <= 0.001, "反推证据与实际视频时长不符")
    boundaries = [*timestamps, end_time]

    shots = []
    next_start = 0
    for shot in brief["shots"]:
        start, end = shot["start_frame"], shot["end_frame"]
        require(type(start) is int and type(end) is int and start == next_start
                and start < end <= media["frames"], "镜头区间必须连续且在底片帧号内")
        require(shot["description"].strip(), "缺少实际镜头描述")
        shots.append({
            **shot, "id": f"S{len(shots) + 1:02d}",
            "start_seconds": boundaries[start], "end_seconds": boundaries[end],
        })
        next_start = end
    require(next_start == media["frames"], "镜头未覆盖底片完整时长")

    speech = brief["speech"]
    require(speech["kind"] in ("speech", "subtitles") and speech["language"].strip()
            and speech["record"].strip(), "缺少口播或字幕的语言与取证记录")
    phrases = read(working / speech["file"])
    require(isinstance(phrases, list) and phrases, "缺少带时码的目标语口播或字幕")
    for phrase in phrases:
        require(0 <= phrase["start"] < phrase["end"] <= media["duration"] + 0.001
                and phrase["text"].strip(), "口播或字幕为空，或超出底片时长")
    claims = brief["selling_points"]
    require(isinstance(claims, list) and claims
            and all(item["claim"].strip() and item["evidence"].strip() for item in claims),
            "商品卖点需要可查的证据")
    requirements = brief["requirements"]
    require(isinstance(requirements, list) and requirements
            and all(item.strip() for item in requirements), "缺少本次短动效要求")

    handoff = working / "handoff.json"
    write(handoff, {
        "status": "ready_for_lemo", "base": {"path": str(base), "sha256": digest, **media},
        "base_review": approval, "shot_evidence": str((working / brief["shot_evidence"]).resolve()),
        "shots": shots, "speech": {**speech, "cues": phrases}, "selling_points": claims,
        "requirements": requirements,
    })
    print(f"交接通过 {handoff}，{len(shots)} 镜，{media['frames']} 帧")


def ensure_frames(film, base, media):
    folder = film / "assets/frames"
    folder.mkdir(parents=True, exist_ok=True)
    receipt = folder / "base.sha256"
    if receipt.exists():
        require(receipt.read_text(encoding="ascii").strip() == media["sha256"],
                "当前动效工程帧缓存来自另一版底片，请用新工程")
    expected = {f"{i:05}.jpg" for i in range(media["frames"])}
    if receipt.exists() and {path.name for path in folder.glob("*.jpg")} == expected:
        return
    run([
        "ffmpeg", "-v", "error", "-xerror", "-nostdin", "-y", "-i", base, "-map", "0:v:0",
        "-fps_mode", "passthrough", "-q:v", "2", "-start_number", "0", folder / "%05d.jpg",
    ])
    require({path.name for path in folder.glob("*.jpg")} == expected, "底片导出的帧序列不完整")
    receipt.write_text(media["sha256"] + "\n", encoding="ascii")


def audio_hash(file, tracks):
    if not tracks:
        return []
    return run([
        "ffmpeg", "-v", "error", "-nostdin", "-i", file, "-map", "0:a",
        "-c", "copy", "-f", "streamhash", "-hash", "sha256", "-",
    ]).splitlines()


def render(handoff_file, filename):
    working = handoff_file.parent
    handoff = read(handoff_file)
    require(handoff["status"] == "ready_for_lemo", "该交接尚未通过")
    media = handoff["base"]
    base = Path(media["path"])
    require(sha256(base) == media["sha256"], "交接后的底片文件有变动")
    require(Path(filename).name == filename and Path(filename).suffix.lower() == ".mp4",
            "成片文件名须为本单目录中的 MP4 文件名")
    output = working / filename
    require(not output.exists(), f"该单已生成成片 {output}")
    film = working / "film"
    timeline = read(film / "timeline.json")
    fps = float(Fraction(media["fps"]))
    require(timeline["base_sha256"] == media["sha256"] and timeline["frames"] == media["frames"]
            and math.isclose(timeline["fps"], fps, abs_tol=0.000001), "动效时间线与底片版本不符")
    labels = timeline["labels"]
    require(isinstance(labels, list) and labels, "当前工程缺少短动效标注")
    for label in labels:
        require(label["text"].strip()
                and 0 <= label["from"] < label["to"] <= media["video_duration"] + 0.001,
                f"标注时码超出底片 {label['id']}")
    width, height = timeline.get("render_size", [media["width"], media["height"]])
    require(width > 0 and height > 0 and width * media["height"] == height * media["width"],
            "渲染尺寸须遵循本次底片的画幅")
    size = f"{width}x{height}"

    components = paths()
    for key in ("lemo_readcheck", "lemo_render"):
        require(components[key].is_file(), f"缺少 Lemo 运行组件 {components[key]}")
    env = lemo_env(components)
    library = components["lemo_rules"].parent
    ensure_frames(film, base, media)
    work = working / "work"
    work.mkdir(exist_ok=True)
    picture = work / f"{output.stem}-picture.mp4"
    candidate = work / f"{output.stem}-check.mp4"
    readcheck = run([
        "node", components["lemo_readcheck"], film,
        "--step", "0.02", "--size", size, "--q", "readcheck=1",
    ], cwd=library, env=env)
    print(readcheck.strip(), flush=True)
    run([
        "node", components["lemo_render"], film, "--fps", str(fps),
        "--size", size, "--out", picture,
    ], cwd=library, env=env, capture=False)
    run([
        "ffmpeg", "-v", "error", "-xerror", "-nostdin", "-y", "-i", picture, "-i", base,
        "-map", "0:v:0", "-map", "1:a?", "-map_metadata", "-1",
        "-vf", "scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p,"
               "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709",
        "-c:v", "libx264", "-crf", "17", "-preset", "fast",
        "-c:a", "copy", "-movflags", "+faststart", candidate,
    ])
    run([
        "ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", candidate, "-map", "0:v:0",
        "-map", "0:a?", "-f", "null", "-",
    ])
    actual = probe(candidate)
    require(actual["frames"] == media["frames"] and (actual["width"], actual["height"]) == (width, height)
            and math.isclose(float(Fraction(actual["fps"])), fps, abs_tol=0.000001)
            and abs(actual["duration"] - media["duration"]) <= 1 / fps
            and abs(actual["video_duration"] - media["video_duration"]) <= 1 / fps,
            "渲染成片帧数、画幅或时长偏离底片")
    before = audio_hash(base, media["audio_tracks"])
    after = audio_hash(candidate, actual["audio_tracks"])
    require(before == after, "渲染后音频轨内容与底片不一致")
    require(sha256(base) == media["sha256"], "底片在渲染期间发生变动")
    result = {
        "status": "technical_checks_passed", "output": str(output), "sha256": sha256(candidate),
        "media": actual, "base_sha256": media["sha256"], "audio_stream_hashes": after,
        "full_decode": True, "lemo_readability": readcheck.splitlines(),
    }
    candidate.rename(output)
    report = "render-check.json" if filename == "final.mp4" else f"{output.stem}-render-check.json"
    write(working / report, result)
    print(f"技术检查通过 {output}，待审看画面与口播同步")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("prepare").add_argument("file", type=Path)
    render_cli = sub.add_parser("render")
    render_cli.add_argument("file", type=Path)
    render_cli.add_argument("--output", default="final.mp4")
    args = cli.parse_args()
    try:
        if args.command == "check":
            check()
        else:
            file = args.file.resolve(strict=True)
            require(file.is_relative_to(ROOT / "runs"), "请在项目 runs 目录内执行本单")
            if args.command == "prepare":
                prepare(file)
            else:
                render(file, args.output)
    except (KeyError, OSError, TypeError, ValueError, RuntimeError) as error:
        cli.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
