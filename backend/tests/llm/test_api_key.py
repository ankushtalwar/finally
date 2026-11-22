"""ANTHROPIC_API_KEY sanitising: values from `docker --env-file` arrive verbatim."""

import pytest

from app.llm import chat


@pytest.fixture(autouse=True)
def _reset_client():
    chat.set_client(None)
    yield
    chat.set_client(None)


@pytest.mark.parametrize(
    "raw",
    [
        "sk-ant-test-key ",
        "sk-ant-test-key\r",
        "  sk-ant-test-key\n",
        '"sk-ant-test-key"',
        "'sk-ant-test-key' ",
    ],
)
def test_key_is_cleaned(monkeypatch, raw):
    monkeypatch.setenv("ANTHROPIC_API_KEY", raw)
    assert chat._clean_api_key() == "sk-ant-test-key"
    client = chat.get_client()
    assert client.api_key == "sk-ant-test-key"
    # httpx rejects header values with surrounding whitespace / CR.
    headers = client.auth_headers
    assert headers
    assert all(v == v.strip() and "sk-ant-test-key" in v for v in headers.values())


def test_missing_or_blank_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert chat._clean_api_key() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", '  ""  ')
    assert chat._clean_api_key() is None
