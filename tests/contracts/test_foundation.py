import pytest
from scoperelay.config import ConfigurationError, Settings
from scoperelay.inference.adapter import LiveInferenceBlocked, ProviderAdapter


def test_safe_defaults(monkeypatch):
    for key in ("SCOPERELAY_MODE", "SCOPERELAY_BIND_HOST"):
        monkeypatch.delenv(key, raising=False)
    assert Settings.from_env().bind_host == "127.0.0.1"


def test_non_loopback_is_rejected(monkeypatch):
    monkeypatch.setenv("SCOPERELAY_BIND_HOST", "0.0.0.0")
    with pytest.raises(ConfigurationError):
        Settings.from_env()


def test_live_provider_is_explicitly_blocked():
    with pytest.raises(LiveInferenceBlocked, match="LIVE_INFERENCE_BLOCKED"):
        ProviderAdapter().invoke([])
