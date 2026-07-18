"""Draw the pixel-space bounding boxes onto a photo, to eyeball the rescaling.

    python scripts/draw_bboxes.py photos/A_90.png [out.png]

Landmarks are drawn in one color and findable objects in another, each labeled.
If the boxes land on the right things, the norm->px rescale is correct. Needs
Qwen credentials for the first run of a given image (then replays from cache).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from PIL import Image, ImageDraw  # noqa: E402

from app.perception import perceive  # noqa: E402
from app.qwen_client import call_qwen  # noqa: E402

LANDMARK_COLOR = (0, 200, 255)
OBJECT_COLOR = (255, 120, 0)


def _draw_box(draw, box, color, label):
    draw.rectangle([box.x_min, box.y_min, box.x_max, box.y_max], outline=color, width=3)
    draw.text((box.x_min + 3, max(0, box.y_min - 12)), label, fill=color)


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print("usage: python scripts/draw_bboxes.py <photo> [out.png]", file=sys.stderr)
        return 2

    photo = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) == 3 else photo.with_name(f"{photo.stem}_bboxes.png")
    if not photo.exists():
        print(f"photo not found: {photo}", file=sys.stderr)
        return 1

    image_bytes = photo.read_bytes()
    img = Image.open(photo).convert("RGB")
    width, height = img.size

    observation = perceive(image_bytes, width, height, call_qwen)
    draw = ImageDraw.Draw(img)

    for landmark in observation.landmarks:
        if landmark.bbox_px is not None:
            _draw_box(draw, landmark.bbox_px, LANDMARK_COLOR, landmark.name)
    for obj in observation.objects:
        if obj.bbox_px is not None:
            _draw_box(draw, obj.bbox_px, OBJECT_COLOR, obj.name)

    img.save(out)
    print(f"wrote {out}  ({len(observation.landmarks)} landmarks, {len(observation.objects)} objects)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
