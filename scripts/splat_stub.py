"""A stand-in for the Gaussian-splat engine, serving the AGREED HTTP contract.

It replays the engine team's real captured frames (sample_json_first_perspective/
*.json) as if the live engine were running, doing the conversion the real /view
endpoint is supposed to do on its side:

  - strip the `data:image/jpeg;base64,` prefix  -> raw base64  ("image_base64")
  - decode each frame to report true width/height (the captures are 640x323,
    variable in principle — NOT a fixed 1280x720, so bbox rescaling must use these)
  - remap three.js Y-up arrays -> our ground frame:  x = threeX, y = threeZ,
    z = threeY(height)
  - heading radians -> yaw_deg
  - keep only genuine first-person frames (view == "ego"): those are the ones
    whose player.heading matches the camera yaw and whose camera sits at the
    avatar. The "camera"/free-cam frames are decoupled from the avatar and are
    not first-person views, so they are dropped.

This lets the agent run end-to-end today on real first-person frames, and serves
as the reference shape the real endpoint must match.

    python scripts/splat_stub.py --port 5173        # -> GET/POST http://localhost:5173/agent/{view,action,reset}

Then point the backend at it:
    WORLD=splat SPLAT_ENGINE_URL=http://localhost:5173/agent uvicorn app.main:app
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "sample_json_first_perspective"


def _load_frames(sample_dir: Path) -> list[dict]:
    """Load and convert every genuine first-person frame into an agreed /view payload."""
    frames: list[dict] = []
    for path in sorted(sample_dir.glob("*.json")):
        export = json.loads(path.read_text())
        for fr in export.get("frames", []):
            if fr.get("view") != "ego":  # drop free-cam frames; keep first-person only
                continue
            img = fr["image"]
            b64 = img.split(",", 1)[1] if img.startswith("data:") else img
            width, height = Image.open(io.BytesIO(base64.b64decode(b64))).size

            ax, ay, az = fr["player"]["avatarPosition"]  # three.js Y-up
            frames.append({
                "image_base64": b64,
                "width": width,
                "height": height,
                "pose": {
                    "x": float(ax),                        # threeX -> x
                    "y": float(az),                        # threeZ -> y (ground)
                    "z": float(ay),                        # threeY -> z (height)
                    "yaw_deg": math.degrees(float(fr["player"]["heading"])),
                },
            })
    return frames


class _StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _frame_payload(self) -> dict:
        srv = self.server
        frame = srv.frames[srv.index]
        return {**frame, "frame_id": srv.index}

    def do_GET(self):
        if self.path.endswith("/view"):
            self._send(200, self._frame_payload())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        srv = self.server

        if self.path.endswith("/action"):
            kind = payload.get("type")
            pose = srv.frames[srv.index]["pose"]
            if kind == "move":
                if srv.index + 1 < len(srv.frames):
                    srv.index += 1  # walk to the next captured spot
                    p = srv.frames[srv.index]["pose"]
                    self._send(200, {"success": True, "pose": p, "message": "moved"})
                else:
                    self._send(200, {"success": False, "pose": pose,
                                     "message": "blocked: end of captured trajectory"})
            elif kind == "turn":
                self._send(200, {"success": True, "pose": pose, "message": "turned"})
            elif kind == "stop":
                self._send(200, {"success": True, "pose": pose, "message": "stopped"})
            else:
                self._send(400, {"error": f"unknown action {kind!r}"})
        elif self.path.endswith("/reset"):
            srv.index = 0
            self._send(200, {"success": True, "pose": srv.frames[0]["pose"], "message": "reset"})
        else:
            self._send(404, {"error": "not found"})


def build_server(port: int, sample_dir: Path = SAMPLE_DIR) -> ThreadingHTTPServer:
    frames = _load_frames(sample_dir)
    if not frames:
        raise SystemExit(f"no frames found in {sample_dir}")
    server = ThreadingHTTPServer(("127.0.0.1", port), _StubHandler)
    server.frames = frames
    server.index = 0
    return server


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5173)
    ap.add_argument("--sample-dir", type=Path, default=SAMPLE_DIR)
    args = ap.parse_args()

    server = build_server(args.port, args.sample_dir)
    print(f"splat stub: {len(server.frames)} frames from {args.sample_dir}")
    print(f"serving  GET/POST http://127.0.0.1:{args.port}/agent/{{view,action,reset}}")
    print("point the backend at it:  "
          f"WORLD=splat SPLAT_ENGINE_URL=http://127.0.0.1:{args.port}/agent uvicorn app.main:app")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
