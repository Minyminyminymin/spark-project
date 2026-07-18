"""Perception rescale math and the malformed-JSON retry path.

These tests use recorded fixture strings and a stub ``qwen_call`` — no network.
"""

from pathlib import Path

import pytest

from app.perception import Observation, PerceptionError, perceive

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VALID_RESPONSE = (FIXTURES / "perception_valid.json").read_text()

# A 640x480 image: chosen so norm->px is exact and easy to check by hand.
WIDTH, HEIGHT = 640, 480
IMAGE = b"fake-image-bytes"


def _stub(*responses):
    """Return a qwen_call stub that yields the given responses in order and
    records how it was invoked."""

    calls = []

    def qwen_call(prompt, image_bytes, json_mode=True):
        calls.append({"prompt": prompt, "image_bytes": image_bytes, "json_mode": json_mode})
        return responses[len(calls) - 1]

    qwen_call.calls = calls
    return qwen_call


def test_returns_validated_observation_and_strips_fences():
    qwen_call = _stub(VALID_RESPONSE)  # fixture is wrapped in ```json fences
    obs = perceive(IMAGE, WIDTH, HEIGHT, qwen_call)

    assert isinstance(obs, Observation)
    assert obs.place_label == "stone_courtyard"
    assert obs.image_width == WIDTH and obs.image_height == HEIGHT
    assert [f.direction for f in obs.frontiers] == ["forward", "left"]
    # json_mode must be requested.
    assert qwen_call.calls[0]["json_mode"] is True


def test_bbox_rescale_math():
    obs = perceive(IMAGE, WIDTH, HEIGHT, _stub(VALID_RESPONSE))

    # red_fountain: norm (250,400,750,900) -> px x*640/1000, y*480/1000
    fountain = obs.landmarks[0]
    assert fountain.name == "red_fountain"
    assert fountain.bbox_norm.model_dump() == {"x_min": 250, "y_min": 400, "x_max": 750, "y_max": 900}
    assert fountain.bbox_px.model_dump() == {"x_min": 160, "y_min": 192, "x_max": 480, "y_max": 432}

    # arched_doorway: norm (800,100,1000,600)
    doorway = obs.landmarks[1]
    assert doorway.bbox_px.model_dump() == {"x_min": 512, "y_min": 48, "x_max": 640, "y_max": 288}

    # object brass_key: norm (500,500,560,540) — exercises rounding
    key = obs.objects[0]
    assert key.bbox_px.model_dump() == {"x_min": 320, "y_min": 240, "x_max": 358, "y_max": 259}


def test_rounding_uses_round_half():
    # norm 501 -> 501/1000*640 = 320.64 -> 321 ; 999 -> 639.36 -> 639
    resp = """{
      "place_label": "p", "place_description": "d",
      "landmarks": [{"name": "n", "description": "d",
        "bbox_norm": {"x_min": 501, "y_min": 0, "x_max": 999, "y_max": 1000}}],
      "objects": [], "frontiers": [], "inferred_heading": "n"
    }"""
    obs = perceive(IMAGE, WIDTH, HEIGHT, _stub(resp))
    assert obs.landmarks[0].bbox_px.model_dump() == {
        "x_min": 321, "y_min": 0, "x_max": 639, "y_max": 480
    }


def test_malformed_json_then_retry_succeeds():
    qwen_call = _stub("not json at all {oops", VALID_RESPONSE)
    obs = perceive(IMAGE, WIDTH, HEIGHT, qwen_call)

    assert isinstance(obs, Observation)
    assert len(qwen_call.calls) == 2
    # The retry appends a correction to the prompt.
    assert "valid JSON" in qwen_call.calls[1]["prompt"]
    assert qwen_call.calls[1]["prompt"] != qwen_call.calls[0]["prompt"]


def test_schema_violation_then_retry_succeeds():
    # Valid JSON, but "frontiers[0].direction" is not in the allowed Literal.
    bad = """{
      "place_label": "p", "place_description": "d", "landmarks": [], "objects": [],
      "frontiers": [{"direction": "up", "description": "x"}], "inferred_heading": "n"
    }"""
    qwen_call = _stub(bad, VALID_RESPONSE)
    obs = perceive(IMAGE, WIDTH, HEIGHT, qwen_call)
    assert isinstance(obs, Observation)
    assert len(qwen_call.calls) == 2


def test_two_failures_raise_perception_error():
    qwen_call = _stub("garbage", "still garbage")
    with pytest.raises(PerceptionError):
        perceive(IMAGE, WIDTH, HEIGHT, qwen_call)
    assert len(qwen_call.calls) == 2
