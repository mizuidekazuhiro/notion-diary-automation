from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.openai_chat_utils import _extract_message_content, chat_completion


class _DummyResponse:
    def __init__(self, *, status: int = 200, payload: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            response = SimpleNamespace(status_code=self.status_code)
            raise requests.HTTPError(response=response)

    def json(self) -> dict:
        return self._payload


def test_extract_message_content_supports_text_array() -> None:
    data = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "hello "},
                        {"type": "text", "text": "world"},
                    ]
                }
            }
        ]
    }
    assert _extract_message_content(data) == "hello world"


def test_chat_completion_uses_base_url(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        return _DummyResponse(payload={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr("scripts.openai_chat_utils.requests.post", fake_post)

    output = chat_completion(model="gpt-test", system_prompt="sys", user_prompt="user")
    assert output == "ok"
    assert seen["url"] == "https://example.test/v1/chat/completions"


def test_chat_completion_retries_on_429(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_post(url, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return _DummyResponse(status=429)
        return _DummyResponse(payload={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "1")
    monkeypatch.setattr("scripts.openai_chat_utils.requests.post", fake_post)
    monkeypatch.setattr("scripts.openai_chat_utils.time.sleep", lambda *_args, **_kwargs: None)

    assert chat_completion(model="gpt-test", system_prompt="sys", user_prompt="user") == "ok"
    assert calls["count"] == 2


def test_chat_completion_missing_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is missing"):
        chat_completion(model="gpt-test", system_prompt="sys", user_prompt="user")
