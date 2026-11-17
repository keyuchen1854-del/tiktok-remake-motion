
"""Prepare portable H3 inputs and submit only the authorized segment."""
from argparse import ArgumentParser
from fractions import Fraction
from hashlib import file_digest
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from remake import FAILED, ffmpeg, probe

HERE = Path(__file__).resolve().parent
CAPSULE = HERE.parent / "vendor" / "capsule-cinema"
ID = re.compile(r"[A-Z][A-Z0-9_-]{0,31}\Z")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def local(base, name):
    relative = Path(name)
    path = (base / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(base.resolve()) or not path.is_file():
        raise ValueError(f"素材须为计划目录内存在的相对路径：{name}")
    return path


def run(script, *args, env=None):
    return subprocess.run([sys.executable, "-X", "utf8", str(script), *map(str, args)],
                          check=True, env=env)


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def timeline(source):
    video, _ = probe(source)
    fps = Fraction(video["avg_frame_rate"])
    frames = int(video["nb_read_frames"])
    if fps <= 0 or fps != Fraction(video["r_frame_rate"]):
        raise ValueError("请提供固定帧率的工作副本")
    if abs(float(video["duration"]) - float(frames / fps)) > float(1 / fps):
        raise ValueError("原片时间线与帧数不一致")
    return fps, frames


def cut_timeline(source, folder, segments, fps):
    folder.mkdir(parents=True, exist_ok=False)
    records = []
    for item in segments:
        sid = item["id"]
        a, b = item["frames"]
        clip = folder / f"{sid}.mp4"
        cut_clip(source, clip, a, b)
        records.append({"id": sid, "frames": [a, b], "file": clip.name, "sha256": digest(clip)})
    write_json(folder / "cuts.json", {
        "source_sha256": digest(source), "fps": str(fps), "segments": records,
    })


def cut_clip(source, clip, a, b):
    ffmpeg("-n", "-i", source, "-vf", f"trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS",
           "-an", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", clip)
    result, has_audio = probe(clip)
    if int(result["nb_read_frames"]) != b-a or has_audio:
        raise ValueError(f"{clip.name} 裁段帧数或音轨不正确")


def early_cut(args):
    path = args.plan.resolve()
    plan = read_json(path)
    source = local(path.parent, plan["source"])
    fps, total = timeline(source)
    validate_ranges(plan["segments"], fps, total)
    cut_timeline(source, args.work.resolve(), plan["segments"], fps)


def validate_ranges(segments, fps, total):
    cursor = 0
    seen = set()
    for item in segments:
        sid = item["id"]
        a, b = item["frames"]
        if not ID.fullmatch(sid) or sid in seen:
            raise ValueError(f"片段编号无效或重复：{sid}")
        if type(a) is not int or type(b) is not int or not cursor == a < b <= total:
            raise ValueError(f"{sid} 的原片帧区间不连续、重叠或越界")
        if not 2 <= (b-a) / fps <= 15:
            raise ValueError(f"{sid} 的参考段须为 2–15 秒。请按渠道能力重新分组")
        seen.add(sid)
        cursor = b
    if cursor != total:
        raise ValueError("计划需覆盖原片全部帧")


def verified_cuts(folder, source_hash, fps, segments):
    if folder is None:
        return {}
    data = read_json(folder / "cuts.json")
    if data["source_sha256"] != source_hash or data["fps"] != str(fps):
        raise ValueError("提前分段与当前原片版本不一致")
    expected = {item["id"]: item["frames"] for item in segments}
    result = {}
    for item in data["segments"]:
        sid = item["id"]
        if expected.get(sid) != item["frames"] or sid in result:
            raise ValueError(f"{sid} 的提前分段与当前时间线不一致")
        clip = local(folder, item["file"])
        if digest(clip) != item["sha256"]:
            raise ValueError(f"{sid} 的提前分段内容已改变")
        result[sid] = clip
    if result.keys() != expected.keys():
        raise ValueError("提前分段缺少当前计划的片段")
    return result


def inspect_plan(path):
    plan = read_json(path)
    base = path.parent
    source = local(base, plan["source"])
    fps, frames = timeline(source)
    mode = plan["mode"]
    if mode not in ("viral", "light"):
        raise ValueError("mode 必须为 viral 或 light")
    refs = {key: local(base, name) for key, name in plan["references"].items()}
    if not refs or any(not ID.fullmatch(key) for key in refs):
        raise ValueError("请输入有效的参考图编号")
    if mode == "light" and len(refs) > 6:
        raise ValueError("轻量版整条视频最多 6 张关键帧")
    for ref in refs.values():
        if ref.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("参考图须为 PNG、JPEG 或 WebP")
        probe(ref)
    checked = []
    validate_ranges(plan["segments"], fps, frames)
    for item in plan["segments"]:
        sid = item["id"]
        duration = item["duration"]
        if type(duration) is not int or not 4 <= duration <= 15:
            raise ValueError(f"{sid} 的适配器输出时长须为 4–15 整数秒")
        prompt = local(base, item["prompt"]).read_text(encoding="utf-8-sig").strip()
        if not prompt:
            raise ValueError(f"{sid} 缺少改写提示词")
        checked.append(dict(item, image=refs[item["reference_id"]], text=prompt))
    pilot = plan.get("representative")
    if mode == "viral" and not any(item["id"] == pilot for item in checked):
        raise ValueError("复刻爆款计划须指定存在的代表段")
    return mode, pilot, source, fps, frames, checked


def prepare(args):
    mode, pilot, source, fps, frames, segments = inspect_plan(args.plan.resolve())
    source_hash = digest(source)
    early = verified_cuts(args.cuts, source_hash, fps, segments)
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    records = []
    for item in segments:
        sid = item["id"]
        a, b = item["frames"]
        folder = work / "segments" / sid
        folder.mkdir(parents=True)
        video = folder / "source-muted.mp4"
        image = folder / ("reference" + item["image"].suffix.lower())
        prompt = folder / "prompt.txt"
        if sid in early:
            shutil.copyfile(early[sid], video)
        else:
            cut_clip(source, video, a, b)
        shutil.copyfile(item["image"], image)
        prompt.write_text(item["text"] + "\n", encoding="utf-8")
        record = {key: item[key] for key in ("id", "frames", "reference_id", "duration")}
        record.update({key: path.relative_to(work).as_posix()
                       for key, path in (("video", video), ("image", image), ("prompt", prompt))})
        record["source_seconds"] = [round(float(frame / fps), 6) for frame in (a, b)]
        record["inputs_sha256"] = {key: digest(path) for key, path in
                                   (("video", video), ("image", image), ("prompt", prompt))}
        records.append(record)
    storyboard = work / "capsule-storyboard.json"
    write_json(storyboard, {"storyboard": [
        {"scene_id": i, "segment_id": item["id"], "chapter_id": "main", "continuity_group": "main",
         "style_anchor": "source-look", "reference_ids": [item["reference_id"]],
         "image_path": item["image"], "source_video_path": item["video"],
         "video_prompt": (work / item["prompt"]).read_text(encoding="utf-8"),
         "duration": float((item["frames"][1]-item["frames"][0]) / fps)}
        for i, item in enumerate(records, start=1)
    ]})
    # Use Capsule's storyboard contract for both routes without loading other account settings.
    env = dict(os.environ, DOTENV_PATH=str(work / "offline-no-dotenv"))
    run(CAPSULE / "scripts" / "validate_storyboard.py", "--storyboard", storyboard,
        "--output", work / "capsule-qa.json", env=env)
    for item in records:
        preview(work, item)
    write_json(work / "handoff.json", {"mode": mode, "representative": pilot,
                                     "fps": str(fps), "source_frames": frames,
                                     "source_sha256": source_hash, "reused_cuts": len(early),
                                     "segments": records})
    print(f"离线准备完成，共 {len(records)} 段。未上传素材。")


def inputs(work, item, image=None, prompt=None):
    return ["--video", local(work, item["video"]), "--image", image or local(work, item["image"]),
            "--prompt", prompt or local(work, item["prompt"]), "--duration", item["duration"]]


def preview(work, item):
    run(HERE / "remake.py", "preview", *inputs(work, item),
        "--output", work / "generated" / (item["id"] + ".mp4"))


def dispatch(args):
    work = args.work.resolve()
    state = read_json(work / "handoff.json")
    item = next((row for row in state["segments"] if row["id"] == args.segment), None)
    if item is None:
        raise ValueError("段号不在已准备的工作清单中")
    directory = work / "generated"
    original = directory / (args.segment + ".mp4")
    output = original.with_name(args.segment + "-retry1.mp4") if args.retry else original
    if args.command == "resume":
        run(HERE / "remake.py", "resume", "--output", output)
        return
    if state["mode"] == "viral":
        if not args.grid_approved:
            raise ValueError("宫格须已获用户确认")
        pilot = state["representative"]
        if item["id"] != pilot:
            files = [directory / (pilot + suffix) for suffix in (".mp4", "-retry1.mp4")]
            if not args.representative_approved or not any(p.is_file() for p in files):
                raise ValueError("剩余段落须等代表段产出并通过验收")
    if args.retry:
        prior_path = original.with_suffix(".state.json")
        if not prior_path.is_file():
            raise ValueError("返工前须有首轮任务记录")
        prior_status = read_json(prior_path).get("status")
        if prior_status not in FAILED | {"downloaded"}:
            raise ValueError("首轮任务仍未确认结束，先用 resume 或查供应商后台")
        if not args.retry_image and not args.retry_prompt:
            raise ValueError("返工须提供修正后的参考图或提示词")
    elif args.retry_image or args.retry_prompt:
        raise ValueError("修正素材仅用于已授权的返工")
    for name, expected in item["inputs_sha256"].items():
        if digest(local(work, item[name])) != expected:
            raise ValueError(f"{item['id']} 的 {name} 已变化，请使用版本化计划重新准备")
    authorization = ["--authorized"] if args.authorized else []
    run(HERE / "remake.py", "submit",
        *inputs(work, item, args.retry_image, args.retry_prompt), "--output", output, *authorization)


def main():
    parser = ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_args = commands.add_parser("prepare")
    prepare_args.add_argument("--plan", required=True, type=Path)
    prepare_args.add_argument("--cuts", type=Path, help="复用已核验的静音分段")
    cut_args = commands.add_parser("cut")
    cut_args.add_argument("--plan", required=True, type=Path)
    cut_args.add_argument("--work", required=True, type=Path)
    for name in ("prepare", "submit", "resume"):
        cmd = prepare_args if name == "prepare" else commands.add_parser(name)
        cmd.add_argument("--work", required=True, type=Path)
        if name != "prepare":
            cmd.add_argument("--segment", required=True)
            cmd.add_argument("--retry", action="store_true")
        if name == "submit":
            for flag in ("grid-approved", "representative-approved", "authorized"):
                cmd.add_argument("--" + flag, action="store_true")
            cmd.add_argument("--retry-image", type=Path)
            cmd.add_argument("--retry-prompt", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "cut":
            early_cut(args)
        elif args.command == "prepare":
            prepare(args)
        else:
            dispatch(args)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"停止：{error}\n")


if __name__ == "__main__":
    main()
