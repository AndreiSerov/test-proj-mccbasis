from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str
    rabbitmq_url: str
    api_key: str

    emulator_min_delay: float = 2
    emulator_max_delay: float = 5
    emulator_success_rate: float = 0.9

    retry_delays: Annotated[tuple[float, ...], NoDecode] = (1, 2)
    webhook_timeout: float = 5
    webhook_allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ()
    """Hostnames or IPs whose Webhooks skip the Blocked address check."""
    consumer_concurrency: int = 10
    graceful_timeout: float = 30
    """Seconds the consumer waits on shutdown for in-flight messages and the Relay."""

    relay_batch_size: int = 100
    relay_poll_interval: float = 0.5
    relay_publish_timeout: float = 5
    """Seconds a publish may wait for the broker's confirm before it fails."""

    @field_validator("api_key")
    @classmethod
    def _api_key_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("API_KEY must not be empty or whitespace")
        return value

    @field_validator("retry_delays", mode="before")
    @classmethod
    def _split_retry_delays(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(float(part) for part in value.split(",") if part.strip())
        return value

    @field_validator("webhook_allowed_hosts", mode="before")
    @classmethod
    def _split_allowed_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(
                part.strip().lower() for part in value.split(",") if part.strip()
            )
        return value


def load_settings() -> Settings:
    """Read settings from the process environment."""
    return Settings()  # pyright: ignore[reportCallIssue]  # required fields come from env
