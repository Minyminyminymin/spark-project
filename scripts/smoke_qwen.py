"""Manual connectivity check for the Qwen client.

Run with real credentials in the environment (see .env.example). It sends one
image plus a trivial prompt and prints the raw response, exercising the whole
path end-to-end.

    python scripts/smoke_qwen.py [path/to/image.png]

With no image path it falls back to a placeholder from photos/. A successful
run records the response into fixtures/, so a later offline replay is possible.
"""

import sys
from pathlib import Path

# Make the project importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional at runtime
    pass

from app.qwen_client import call_qwen  # noqa: E402

DEFAULT_IMAGE = Path(__file__).resolve().parent.parent / "photos" / "A_90.png"
PROMPT = "Reply with a one-sentence description of what you see in this image."


def main() -> int:
    image_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_IMAGE
    if not image_path.exists():
        print(f"image not found: {image_path}", file=sys.stderr)
        return 1

    image_bytes = image_path.read_bytes()
    print(f"Sending {image_path} ...", file=sys.stderr)
    response = call_qwen(PROMPT, image_bytes=image_bytes, json_mode=False)
    print("--- raw response ---")
    print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
