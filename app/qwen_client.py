"""Thin wrapper over a hosted OpenAI-compatible chat-completions endpoint.

Configuration comes entirely from the environment:

    QWEN_API_BASE   base URL of the OpenAI-compatible endpoint
    QWEN_API_KEY    bearer token
    QWEN_MODEL      model string (see .env.example — the HF router needs a
                    provider suffix such as ":together")
    QWEN_OFFLINE    "1" forbids network access; a cache miss then raises

The wrapper records every response to disk (``fixtures/``) keyed by a hash of
the request, and replays from that cache on a hit with no network call. This
makes tests deterministic and lets a captured session run fully offline.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import time
from pathlib import Path

from openai import APIConnectionError, APITimeoutError, OpenAI
from PIL import Image

# Directory holding recorded responses. Module-level so tests can redirect it.
_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

# Backoff before the single retry, in seconds.
_RETRY_BACKOFF_SECONDS = 1.0

# Images are downscaled/re-encoded before upload so raw phone photos don't blow
# past the router's request-size limit (a multi-MB JPEG base64-inflates by ~33%
# and returns HTTP 413). Qwen emits boxes on a resolution-independent 0-1000
# scale, so shrinking the transported image does not shift any bbox.
_MAX_IMAGE_EDGE = 1280
_JPEG_QUALITY = 85


class QwenOfflineError(RuntimeError):
    """Raised on a cache miss while QWEN_OFFLINE=1."""


def call_qwen(prompt: str, image_bytes: bytes | None = None, json_mode: bool = False) -> str:
    """Send ``prompt`` (optionally with an image) and return the raw text reply.

    On a cache hit the recorded response is returned with no network call. On a
    miss with ``QWEN_OFFLINE=1`` this raises :class:`QwenOfflineError`;
    otherwise it calls the network (retrying once on transient errors) and
    records the response before returning it.
    """

    model = os.environ["QWEN_MODEL"]
    cache_path = _cache_path(prompt, image_bytes, model)

    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    if os.environ.get("QWEN_OFFLINE") == "1":
        raise QwenOfflineError(
            f"cache miss for key {cache_path.stem} while QWEN_OFFLINE=1"
        )

    response = _call_network(prompt, image_bytes, model, json_mode)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(response, encoding="utf-8")
    return response


def _cache_key(prompt: str, image_bytes: bytes | None, model: str) -> str:
    """SHA256 over (prompt + image bytes + model)."""

    digest = hashlib.sha256()
    digest.update(prompt.encode("utf-8"))
    if image_bytes:
        digest.update(image_bytes)
    digest.update(model.encode("utf-8"))
    return digest.hexdigest()


def _cache_path(prompt: str, image_bytes: bytes | None, model: str) -> Path:
    return _FIXTURES_DIR / f"{_cache_key(prompt, image_bytes, model)}.txt"


def _prepare_image(image_bytes: bytes) -> bytes:
    """Downscale to a max edge and re-encode as JPEG to keep the upload small."""

    with Image.open(io.BytesIO(image_bytes)) as img:
        img = img.convert("RGB")
        img.thumbnail((_MAX_IMAGE_EDGE, _MAX_IMAGE_EDGE))  # preserves aspect ratio
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
        return buffer.getvalue()


def _build_messages(prompt: str, image_bytes: bytes | None) -> list:
    content: list = [{"type": "text", "text": prompt}]
    if image_bytes:
        prepared = _prepare_image(image_bytes)
        b64 = base64.b64encode(prepared).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )
    return [{"role": "user", "content": content}]


def _call_network(
    prompt: str, image_bytes: bytes | None, model: str, json_mode: bool
) -> str:
    """Call the endpoint, retrying once with backoff on transient errors."""

    client = OpenAI(
        base_url=os.environ["QWEN_API_BASE"],
        api_key=os.environ["QWEN_API_KEY"],
    )
    messages = _build_messages(prompt, image_bytes)
    kwargs: dict = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            completion = client.chat.completions.create(
                model=model, messages=messages, **kwargs
            )
            return completion.choices[0].message.content or ""
        except (APIConnectionError, APITimeoutError) as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(_RETRY_BACKOFF_SECONDS)

    assert last_exc is not None
    raise last_exc
