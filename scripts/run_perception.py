"""Run perception on one static photo and print the validated Observation.

    python scripts/run_perception.py photos/A_90.png

Requires real Qwen credentials in the environment (see .env.example) on the
first run for a given image; the response is cached to fixtures/, so re-runs
replay offline.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from PIL import Image  # noqa: E402

from app.perception import perceive  # noqa: E402
from app.qwen_client import call_qwen  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/run_perception.py <photo>", file=sys.stderr)
        return 2

    photo = Path(sys.argv[1])
    if not photo.exists():
        print(f"photo not found: {photo}", file=sys.stderr)
        return 1

    image_bytes = photo.read_bytes()
    with Image.open(photo) as img:
        width, height = img.size

    observation = perceive(image_bytes, width, height, call_qwen)
    print(observation.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
