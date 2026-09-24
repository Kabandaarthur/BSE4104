"""Retry/error behaviour of the chat client (transient 429/5xx vs hard errors)."""

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import model_client
from model_client import ModelClientError
from openai import BadRequestError, InternalServerError, RateLimitError


def _api_error(status: int):
    request = httpx.Request("POST", "http://model.test/chat")
    response = httpx.Response(status, request=request)
    if status == 429:
        return RateLimitError("rate limited", response=response, body=None)
    if status == 400:
        return BadRequestError("bad request", response=response, body=None)
    return InternalServerError("high demand", response=response, body=None)


def _fake_openai_client(create):
    class _Completions:
        def create(self, **kwargs):
            return create(**kwargs)

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self):
            self.chat = _Chat()

    return _Client()


def _ok_message(text="ok"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _run_chat(create, attempt_cap, monkeypatch):
    calls = {"n": 0}
    sleeps = []

    monkeypatch.setattr(model_client, "CHAT_RETRY_ATTEMPTS", attempt_cap)
    monkeypatch.setattr(model_client, "CHAT_RETRY_BACKOFF", 1)
    monkeypatch.setattr(
        model_client,
        "get_client",
        lambda provider: _fake_openai_client(
            lambda **kwargs: (calls.__setitem__("n", calls["n"] + 1) or create(**kwargs))
        ),
    )
    monkeypatch.setattr(model_client.time, "sleep", lambda s: sleeps.append(s))
    return calls, sleeps


@pytest.mark.parametrize("status", [429, 503])
def test_retries_transient_then_succeeds(status, monkeypatch):
    first = {"done": False}

    def create(**kwargs):
        if not first["done"]:
            first["done"] = True
            raise _api_error(status)
        return _ok_message()

    calls, sleeps = _run_chat(create, 5, monkeypatch)
    message = model_client.chat_completion(
        [{"role": "user", "content": "hello"}]
    )
    assert message.content == "ok"
    assert calls["n"] == 2
    assert sleeps == [1]


def test_no_retry_on_non_transient_error(monkeypatch):
    def create(**kwargs):
        raise _api_error(400)

    calls, sleeps = _run_chat(create, 5, monkeypatch)
    with pytest.raises(ModelClientError):
        model_client.chat_completion([{"role": "user", "content": "hello"}])
    assert calls["n"] == 1
    assert sleeps == []


def test_exhausted_retries_raise_modelclienterror(monkeypatch):
    def create(**kwargs):
        raise _api_error(503)

    calls, sleeps = _run_chat(create, 3, monkeypatch)
    with pytest.raises(ModelClientError):
        model_client.chat_completion([{"role": "user", "content": "hello"}])
    assert calls["n"] == 3
    assert sleeps == [1, 2]