"""Prepare local frame sheets, edit candidates and optional speech for video review."""

import argparse
from bisect import bisect_left
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
from time import perf_counter

from PIL import Image, ImageDraw


FRAME_INFO = re.compile(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\b(?:\s+pts_time:\S+\s+duration:\s*(\d+))?")
SCENE_SCORE = re.compile(r"lavfi\.scd\.score:\s*([0-9.]+),\s*lavfi\.scd\.time:")
TIME_BASE = re.compile(r"config in time_base:\s*(\d+)/(\d+)")
FFMPEG = ["ffmpeg", "-nostdin", "-hide_banner", "-nostats"]
LOCAL_INPUT = ["-protocol_whitelist", "file,pipe"]


def execute(command):
    result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else str(command[0]))
    return result


def scan(source, threshold):
    log = execute(
        FFMPEG + ["-v", "info", "-copyts"] + LOCAL_INPUT + ["-i", str(source), "-map", "0:V:0", "-an",
                  "-vf", f"scale=270:-2,scdet=t={threshold:g},showinfo=checksum=0",
                  "-fps_mode", "passthrough", "-f", "null", "-"]
    ).stderr
    base = TIME_BASE.search(log)
    if base is None or int(base[2]) == 0:
        raise ValueError("Could not read the video filter time base.")
    tick = int(base[1]) / int(base[2])
    points, candidates = [], []
    score = None
    last_duration = 0
    for line in log.splitlines():
        match = SCENE_SCORE.search(line)
        if match:
            score = float(match[1])
        frame = FRAME_INFO.search(line)
        if frame:
            index, point = int(frame[1]), int(frame[2])
            if index != len(points) or (points and point <= points[-1]):
                raise ValueError("Source frame numbers or presentation timestamps are discontinuous.")
            points.append(point)
            last_duration = int(frame[3] or 0)
            if score is not None and index > 0:
                candidates.append((index, score))
            score = None
    if not points:
        raise ValueError("No video frames found.")
    return [(point - points[0]) * tick for point in points], candidates, points[0] * tick, last_duration * tick


def parse_frames(spec, count):
    frames = set()
    for item in spec.split(",") if spec else []:
        parts = list(map(int, item.split(":")))
        if len(parts) == 1:
            frames.add(parts[0])
            continue
        if len(parts) not in (2, 3):
            raise ValueError("Use comma-separated frame numbers or first:last[:step] ranges.")
        first, last, *step = parts
        stride = step[0] if step else 1
        if not 0 <= first <= last < count or stride < 1:
            raise ValueError(f"Supplemental frame ranges must be within 0–{count - 1}.")
        frames.update(range(first, last + 1, stride))
    if any(not 0 <= frame < count for frame in frames):
        raise ValueError(f"Supplemental frame numbers must be within 0–{count - 1}.")
    return sorted(frames)


def frame_filter(indices):
    if len(indices) > 1200:
        raise ValueError("Too many frames for a single review. Use a larger step or a shorter local excerpt.")
    ranges = []
    for index in indices:
        if ranges and index == ranges[-1][1] + 1:
            ranges[-1][1] = index
        else:
            ranges.append([index, index])
    terms = [
        f"eq(n,{first})" if first == last else f"between(n,{first},{last})"
        for first, last in ranges
    ]
    # FFmpeg's expression evaluator limits recursive evaluation depth.
    while len(terms) > 1:
        terms = [
            f"({terms[i]}+{terms[i + 1]})" if i + 1 < len(terms) else terms[i]
            for i in range(0, len(terms), 2)
        ]
    return terms[0]


def render_sheets(source, output, times, groups):
    requested = sorted({frame for group in groups.values() for frame in group})
    select = frame_filter(requested)
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        filters = (f"select='{select}',"
                   "scale=180:298:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1")
        execute(FFMPEG + ["-v", "error"] + LOCAL_INPUT + ["-i", str(source), "-map", "0:V:0", "-an", "-vf", filters,
                          "-fps_mode", "passthrough", "-q:v", "3", str(work / "%06d.jpg")])
        files = sorted(work.glob("*.jpg"))
        if len(files) != len(requested):
            raise ValueError("The number of extracted images differs from the requested frame count.")
        by_frame = dict(zip(requested, files))
        manifest = {}
        for group, frames in groups.items():
            pages = []
            for offset in range(0, len(frames), 12):
                chunk = frames[offset : offset + 12]
                sheet = Image.new("RGB", (720, 320 * math.ceil(len(chunk) / 4)), "#151820")
                draw = ImageDraw.Draw(sheet)
                for slot, frame in enumerate(chunk):
                    x, y = 180 * (slot % 4), 320 * (slot // 4)
                    draw.text((x + 5, y + 4), f"{times[frame]:.3f}s  f{frame:05d}", fill="white")
                    with Image.open(by_frame[frame]) as tile:
                        sheet.paste(tile, (x + (180 - tile.width) // 2, y + 20))
                name = f"{group}-{len(pages) + 1:02d}.jpg"
                sheet.save(output / name, quality=91)
                pages.append({"file": name, "frames": chunk})
            manifest[group] = pages
        return manifest


def read_streams(source):
    result = execute(["ffprobe", "-v", "error"] + LOCAL_INPUT + ["-show_entries",
                      "stream=codec_type,start_time,duration:stream_disposition=attached_pic,timed_thumbnails",
                      "-of", "json", str(source)])
    streams = json.loads(result.stdout)["streams"]
    eligible = (
        s for s in streams if s["codec_type"] == "video"
        and not any(s.get("disposition", {}).get(key) for key in ("attached_pic", "timed_thumbnails"))
    )
    video = next(eligible, None)
    if video is None:
        raise ValueError("No regular video stream found.")
    audio = next((s for s in streams if s["codec_type"] == "audio"), None)
    return video, audio


def numeric(value):
    return float(value) if value not in (None, "N/A") else None


def transcribe(source, model_path, audio_stream, origin):
    if audio_stream is None:
        return {"status": "no_audio"}
    if model_path is None:
        return {"status": "not_requested"}
    if not all((model_path / name).is_file() for name in ("model.bin", "tokenizer.json")):
        return {"status": "failed", "error": "Pass a local model folder containing model.bin and tokenizer.json."}
    try:
        from faster_whisper import WhisperModel

        with tempfile.TemporaryDirectory() as scratch:
            wav = Path(scratch) / "speech.wav"
            execute(FFMPEG + ["-v", "error"] + LOCAL_INPUT + ["-i", str(source), "-map", "0:a:0",
                              "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)])
            model = WhisperModel(str(model_path.resolve()), device="cpu", compute_type="int8",
                                 cpu_threads=4, local_files_only=True)
            rows, info = model.transcribe(str(wav), beam_size=3, word_timestamps=True, vad_filter=False)
            audio_start = numeric(audio_stream.get("start_time"))
            shift = audio_start - origin if audio_start is not None else 0
            segments = [
                {"start": round(row.start + shift, 4), "end": round(row.end + shift, 4), "text": row.text.strip(),
                 "words": [{"start": round(word.start + shift, 4), "end": round(word.end + shift, 4),
                            "text": word.word} for word in row.words or []]}
                for row in rows
            ]
            return {"status": "transcribed", "language": info.language, "start_offset_seconds": shift,
                    "alignment": "approximate_stream_start" if audio_start is not None else "audio_relative",
                    "segments": segments}
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        return {"status": "failed", "error": str(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="local video file")
    parser.add_argument("output", type=Path, help="new output folder")
    parser.add_argument("--step", type=float, default=1.0, help="seconds between overview samples")
    parser.add_argument("--threshold", type=float, default=2.0, help="FFmpeg scene score threshold in percent")
    parser.add_argument("--frames", help="supplemental source frame numbers, e.g. 11,58:65:2")
    parser.add_argument("--whisper-model", type=Path, help="optional existing local faster-whisper model folder")
    args = parser.parse_args()
    if not (math.isfinite(args.step) and args.step >= 0.05 and 0 < args.threshold <= 100):
        parser.error("Use a finite step of at least 0.05s and a threshold within (0, 100].")
    start = perf_counter()
    source = args.source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("Use a local video file.")
    if args.output.exists():
        raise FileExistsError(f"Output already exists: {args.output}")
    times, peaks, origin, last_duration = scan(source, args.threshold)
    scanned = perf_counter()
    video_stream, audio_stream = read_streams(source)
    duration = times[-1] + last_duration
    basis = "last_frame_duration"
    if last_duration <= 0:
        start_stamp = numeric(video_stream.get("start_time"))
        stream_length = numeric(video_stream.get("duration"))
        if start_stamp is None or stream_length is None or start_stamp + stream_length - origin <= times[-1]:
            raise ValueError("Unable to verify the last frame duration.")
        duration = start_stamp + stream_length - origin
        basis = "video_stream_end"
    with source.open("rb") as raw:
        digest = hashlib.file_digest(raw, "sha256").hexdigest()

    sample_count = int(times[-1] / args.step)
    if sample_count > 1200:
        raise ValueError("Too many overview samples. Increase --step or use a shorter local excerpt.")
    overview = {0, len(times) - 1}
    overview.update(bisect_left(times, slot * args.step) for slot in range(1, sample_count + 1))
    cuts = [
        {"frame_before": index - 1, "frame_after": index, "score": score,
         "before_seconds": round(times[index - 1], 6), "after_seconds": round(times[index], 6)}
        for index, score in peaks
    ]
    groups = {
        "overview": sorted(overview),
        "cut-pairs": [item for row in cuts for item in (row["frame_before"], row["frame_after"])],
        "extra": parse_frames(args.frames, len(times)),
    }
    args.output.mkdir(parents=True, exist_ok=False)
    sheets = render_sheets(source, args.output, times, groups)
    visual_done = perf_counter()
    audio = transcribe(source, args.whisper_model, audio_stream, origin)
    done = perf_counter()
    record = {
        "source": str(source),
        "source_sha256": digest,
        "frame_count": len(times),
        "frame_times_seconds": [round(point, 6) for point in times],
        "video_start_pts_seconds": origin,
        "duration_seconds": round(duration, 6),
        "duration_basis": basis,
        "candidate_detector": {"filter": "scale=270:-2,scdet", "threshold_percent": args.threshold},
        "candidate_cuts": cuts,
        "sheets": sheets,
        "audio": audio,
        "timing": {"scan_seconds": round(scanned - start, 3), "visual_total_seconds": round(visual_done - start, 3),
                   "audio_seconds": round(done - visual_done, 3), "total_seconds": round(done - start, 3)},
    }
    with (args.output / "evidence.json").open("x", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"frames": len(times), "candidate_count": len(cuts), "audio": audio["status"],
                      **record["timing"]}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"Video evidence: {error}") from error
