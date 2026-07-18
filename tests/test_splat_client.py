"""SplatWorld against a mock engine implementing the agreed HTTP contract."""

import base64
import io
import json
import math
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from app.controller import Agent
from app.memory import TopoMap
from app.perception import perceive
from app.world.base import MoveAction, StopAction, TurnAction
from app.world.splat_client import SplatEngineError, SplatWorld


def _jpeg_b64(w=1280, h=720):
    img = Image.new("RGB", (w, h), (40, 80, 120))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


_FRAME_B64 = _jpeg_b64()
WALL_Y = -3.0  # a "wall" the BVH collider would stop the avatar at


class _EngineHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/view"):
            av = self.server.avatar
            self._send(200, {
                "image_base64": _FRAME_B64, "width": 1280, "height": 720,
                "pose": dict(av["pose"]), "frame_id": av["frame"],
            })
            av["frame"] += 1
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        av = self.server.avatar
        if self.path.endswith("/action"):
            self._action(payload, av["pose"])
        elif self.path.endswith("/reset"):
            av["pose"] = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw_deg": 0.0}
            self._send(200, {"success": True, "pose": dict(av["pose"]), "message": "reset"})
        else:
            self._send(404, {"error": "not found"})

    def _action(self, payload, pose):
        kind = payload.get("type")
        if kind == "turn":  # +degrees turns left (CCW), per contract
            pose["yaw_deg"] += float(payload.get("degrees", 0.0))
            self._send(200, {"success": True, "pose": dict(pose), "message": "turned"})
        elif kind == "move":
            d = float(payload.get("distance", 0.0))
            yaw = math.radians(pose["yaw_deg"])
            new_x = pose["x"] - math.sin(yaw) * d          # forward = (-sin, -cos)
            new_y = pose["y"] - math.cos(yaw) * d
            if new_y < WALL_Y:                              # collider stops us short
                pose["y"] = WALL_Y
                self._send(200, {"success": False, "pose": dict(pose), "message": "blocked"})
            else:
                pose["x"], pose["y"] = new_x, new_y
                self._send(200, {"success": True, "pose": dict(pose), "message": "moved"})
        elif kind == "stop":
            self._send(200, {"success": True, "pose": dict(pose), "message": "stopped"})
        else:
            self._send(400, {"error": "bad action"})


@pytest.fixture
def engine_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EngineHandler)
    server.avatar = {"pose": {"x": 0.0, "y": 0.0, "z": 0.0, "yaw_deg": 0.0}, "frame": 0}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/agent"
    finally:
        server.shutdown()


def _dead_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    return f"http://127.0.0.1:{port}/agent"


# --------------------------------------------------------------------------- #
# Unit-level contract behavior
# --------------------------------------------------------------------------- #


def test_get_current_view_decodes_frame_and_pose(engine_url):
    world = SplatWorld(engine_url)
    view = world.get_current_view()

    assert (view.width, view.height) == (1280, 720)
    assert Image.open(io.BytesIO(view.image)).size == (1280, 720)  # real JPEG bytes
    assert (view.pose.x, view.pose.y, view.pose.yaw_deg) == (0.0, 0.0, 0.0)
    assert view.frame_id == 0
    assert world.get_current_view().frame_id == 1                   # increments


def test_move_and_turn_update_pose(engine_url):
    world = SplatWorld(engine_url)
    # yaw 0 faces -y, so a move decreases y.
    res = world.execute_action(MoveAction(distance=1.0))
    assert res.success is True
    assert res.pose.y == pytest.approx(-1.0)

    # +90 turns left (CCW); forward becomes -x.
    world.execute_action(TurnAction(degrees=90))
    res = world.execute_action(MoveAction(distance=1.0))
    assert res.success is True
    assert res.pose.x == pytest.approx(-1.0)


def test_blocked_move_is_success_false_not_an_exception(engine_url):
    world = SplatWorld(engine_url)
    last = None
    for _ in range(6):  # walk into the wall at y = -3
        last = world.execute_action(MoveAction(distance=1.0))
    assert last.success is False
    assert last.message == "blocked"
    assert last.pose.y == pytest.approx(WALL_Y)


def test_stop_action_acknowledged(engine_url):
    world = SplatWorld(engine_url)
    res = world.execute_action(StopAction(reason="done"))
    assert res.success is True


def test_action_transport_error_becomes_failure_not_exception():
    world = SplatWorld(_dead_url())
    res = world.execute_action(MoveAction(distance=1.0))  # must not raise
    assert res.success is False
    assert "splat engine error" in res.message


def test_get_view_fails_loud_when_engine_down():
    world = SplatWorld(_dead_url())
    with pytest.raises(SplatEngineError):
        world.get_current_view()


def test_reset_returns_home_pose(engine_url):
    world = SplatWorld(engine_url)
    world.execute_action(MoveAction(distance=1.0))
    res = world.reset()
    assert res.success is True
    assert (res.pose.x, res.pose.y) == (0.0, 0.0)


# --------------------------------------------------------------------------- #
# Splat frame -> perception -> rescaled boxes (the phase-2 verification)
# --------------------------------------------------------------------------- #


def test_splat_frame_flows_through_perception_with_rescaled_boxes(engine_url):
    world = SplatWorld(engine_url)
    view = world.get_current_view()

    response = (
        '{"place_label":"room","place_description":"a scanned room",'
        '"landmarks":[{"name":"pillar","description":"a stone pillar",'
        '"bbox_norm":{"x_min":500,"y_min":500,"x_max":600,"y_max":600}}],'
        '"objects":[],"frontiers":[{"direction":"forward","description":"ahead"}],'
        '"inferred_heading":"north"}'
    )
    obs = perceive(view.image, view.width, view.height, lambda p, b, json_mode=True: response)

    # 0-1000 norm rescaled against the true 1280x720 frame.
    assert obs.landmarks[0].bbox_px.model_dump() == {
        "x_min": 640, "y_min": 360, "x_max": 768, "y_max": 432
    }


# --------------------------------------------------------------------------- #
# End-to-end: a 10-turn agent run over HTTP produces a coherent graph
# --------------------------------------------------------------------------- #


def _perception(label):
    return json.dumps({
        "place_label": label, "place_description": f"room {label}",
        "landmarks": [], "objects": ([] if label != "s9" else
            [{"name": "red_mug", "description": "the mug",
              "bbox_norm": {"x_min": 500, "y_min": 500, "x_max": 560, "y_max": 560}}]),
        "frontiers": [{"direction": "forward", "description": "ahead"},
                      {"direction": "left", "description": "left"}],
        "inferred_heading": "north",
    })


def _plan(expected, status="searching", stop=False):
    queue = [{"type": "stop", "reason": "found it"}] if stop else [{"type": "move", "distance": 1.0}]
    return json.dumps({"reasoning": "step", "action_queue": queue,
                       "expected_next_node": expected, "goal_status": status})


class _ScriptedQwen:
    def __init__(self, perception, plan):
        self._p = list(perception)
        self._q = list(plan)

    def __call__(self, prompt, image_bytes, json_mode=True):
        return self._q.pop(0) if image_bytes is None else self._p.pop(0)


def test_ten_turn_run_over_http_builds_coherent_graph(engine_url, tmp_path):
    labels = [f"s{i}" for i in range(10)]
    perception = [_perception(l) for l in labels]
    plan = [_plan(labels[k + 1]) for k in range(9)] + [_plan("s9", "found", stop=True)]

    agent = Agent(SplatWorld(engine_url), TopoMap(), "find the red mug",
                  _ScriptedQwen(perception, plan), tmp_path / "splat_log.jsonl")
    records = agent.run(max_turns=20)

    assert agent.done is True
    assert records[-1]["goal_status"] == "found"
    assert agent.topo_map.node_count == 10           # ten distinct places
    assert agent.topo_map.g.number_of_edges() == 9   # a connected traversal chain
    assert all(r["type"] == "decision" for r in records)
