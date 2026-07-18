"""The Qwen client records on the first call and replays offline thereafter."""

import pytest

from app import qwen_client


def test_records_on_first_call_then_replays_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(qwen_client, "_FIXTURES_DIR", tmp_path)
    monkeypatch.setenv("QWEN_MODEL", "test-model:provider")

    calls = {"n": 0}

    def fake_network(prompt, image_bytes, model, json_mode):
        calls["n"] += 1
        return "recorded-response"

    monkeypatch.setattr(qwen_client, "_call_network", fake_network)

    # First call: cache miss -> network -> record.
    first = qwen_client.call_qwen("hello", image_bytes=b"\x89PNG-bytes", json_mode=False)
    assert first == "recorded-response"
    assert calls["n"] == 1
    assert len(list(tmp_path.glob("*.txt"))) == 1  # response was recorded

    # Second call, now offline: cache hit -> no network call.
    monkeypatch.setenv("QWEN_OFFLINE", "1")
    second = qwen_client.call_qwen("hello", image_bytes=b"\x89PNG-bytes", json_mode=False)
    assert second == "recorded-response"
    assert calls["n"] == 1  # network was NOT called again


def test_offline_cache_miss_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(qwen_client, "_FIXTURES_DIR", tmp_path)
    monkeypatch.setenv("QWEN_MODEL", "test-model:provider")
    monkeypatch.setenv("QWEN_OFFLINE", "1")

    # Guard: network must never be reached while offline.
    def boom(*args, **kwargs):
        raise AssertionError("network must not be called while QWEN_OFFLINE=1")

    monkeypatch.setattr(qwen_client, "_call_network", boom)

    with pytest.raises(qwen_client.QwenOfflineError):
        qwen_client.call_qwen("never-seen-prompt", image_bytes=None, json_mode=False)


def test_cache_key_depends_on_prompt_image_and_model():
    base = qwen_client._cache_key("p", b"img", "m")
    assert base != qwen_client._cache_key("p2", b"img", "m")
    assert base != qwen_client._cache_key("p", b"img2", "m")
    assert base != qwen_client._cache_key("p", b"img", "m2")
    # Stable and hex-encoded SHA256.
    assert base == qwen_client._cache_key("p", b"img", "m")
    assert len(base) == 64
