# Helpers adapted from the Capsule Cinema local Metaso H3 adapter.
# Distributed with the accompanying Apache 2.0 license.
import base64
import mimetypes
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

DEFAULT_MODEL = "MiniMax-H3"

def _http_url(value: Optional[str], name: str) -> Optional[str]:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} must be a provider-accessible HTTP(S) URL")
    return value


def _file_data_url(value: str, name: str, allowed_mime_prefix: str) -> str:
    path = Path(value).expanduser()
    if not path.is_file():
        raise ValueError(f"{name} must be an existing local file")
    mime_type = mimetypes.guess_type(path.name)[0] or ""
    if not mime_type.startswith(allowed_mime_prefix):
        raise ValueError(f"{name} must be a supported {allowed_mime_prefix.rstrip('/')} file")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def build_metaso_h3_payload(
    *,
    prompt: str,
    model: str = DEFAULT_MODEL,
    reference_image_urls: Optional[list[str]] = None,
    reference_image_paths: Optional[list[str]] = None,
    reference_video_url: Optional[str] = None,
    reference_video_path: Optional[str] = None,
    reference_audio_url: Optional[str] = None,
    resolution: str = "768P",
    duration: int = 5,
    ratio: str = "adaptive",
) -> dict[str, Any]:
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    if int(duration) <= 0:
        raise ValueError("duration must be positive")
    image_urls = [
        _http_url(value, f"reference_image_urls[{index}]")
        for index, value in enumerate(reference_image_urls or [])
    ]
    image_urls.extend(
        _file_data_url(value, f"reference_image_paths[{index}]", "image/")
        for index, value in enumerate(reference_image_paths or [])
    )
    if reference_video_url and reference_video_path:
        raise ValueError("use either reference_video_url or reference_video_path, not both")
    video_url = _http_url(reference_video_url, "reference_video_url")
    if reference_video_path:
        video_url = _file_data_url(reference_video_path, "reference_video_path", "video/")
    audio_url = _http_url(reference_audio_url, "reference_audio_url")
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt.strip()}]
    for image_url in image_urls:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": image_url},
                "role": "reference_image",
            }
        )
    if video_url:
        content.append(
            {
                "type": "video_url",
                "video_url": {"url": video_url},
                "role": "reference_video",
            }
        )
    if audio_url:
        content.append(
            {
                "type": "audio_url",
                "audio_url": {"url": audio_url},
                "role": "reference_audio",
            }
        )
    return {
        "model": model,
        "content": content,
        "resolution": resolution,
        "duration": int(duration),
        "ratio": ratio,
    }


def _find_first(payload: Any, keys: tuple[str, ...]) -> str:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value)
        for value in payload.values():
            found = _find_first(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_first(value, keys)
            if found:
                return found
    return ""


def _find_video_url(payload: Any) -> str:
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, dict):
            content_url = content.get("url")
            if isinstance(content_url, str) and content_url.startswith(("https://", "http://")):
                return content_url
        for key in ("video_url", "output_url", "download_url", "file_url", "url"):
            value = payload.get(key)
            if isinstance(value, dict):
                value = value.get("url")
            if not isinstance(value, str) or not value.startswith(("https://", "http://")):
                continue
            path = urlparse(value).path.lower()
            if key != "url" or path.endswith((".mp4", ".mov", ".webm")):
                return value
        for value in payload.values():
            found = _find_video_url(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_video_url(value)
            if found:
                return found
    return ""


def _task_record(payload: Any, task_id: str) -> dict[str, Any]:
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                identifier = str(item.get("id") or item.get("task_id") or "")
                if identifier == task_id:
                    return item
        identifier = str(payload.get("id") or payload.get("task_id") or "")
        if identifier == task_id:
            return payload
    return {}
