from dataclasses import dataclass
import os


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    mode: str = "synthetic-local"
    provider: str = "none"
    bind_host: str = "127.0.0.1"
    port: int = 8765
    model_base_url: str = ""
    model_id: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("SCOPERELAY_MODE", "synthetic-local")
        provider = os.getenv("SCOPERELAY_PROVIDER", "none")
        host = os.getenv("SCOPERELAY_BIND_HOST", "127.0.0.1")
        if mode != "synthetic-local":
            raise ConfigurationError("only synthetic-local mode is supported")
        if host != "127.0.0.1":
            raise ConfigurationError("P0 foundation must bind to loopback")
        try:
            port = int(os.getenv("SCOPERELAY_PORT", "8765"))
        except ValueError as exc:
            raise ConfigurationError("port must be an integer") from exc
        if not 1024 <= port <= 65535:
            raise ConfigurationError("port must be a local high port")
        return cls(
            mode,
            provider,
            host,
            port,
            os.getenv("SCOPERELAY_MODEL_BASE_URL", ""),
            os.getenv("SCOPERELAY_MODEL_ID", ""),
        )
