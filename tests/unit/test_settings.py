import pytest
from pydantic import ValidationError

from payments.core.settings import Settings, load_settings

DB = "postgresql+asyncpg://unused@localhost/unused"
MQ = "amqp://unused@localhost/"


def test_default_retry_delays_give_three_attempts() -> None:
    settings = Settings(database_url=DB, rabbitmq_url=MQ, api_key="k")

    assert settings.retry_delays == (1, 2)


@pytest.mark.parametrize("key", ["", "  ", "\t\n"], ids=["empty", "spaces", "tabs"])
def test_empty_or_blank_api_key_fails_at_start_up(key: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=DB, rabbitmq_url=MQ, api_key=key)


def test_emulator_failure_mode_defaults_to_decline() -> None:
    settings = Settings(database_url=DB, rabbitmq_url=MQ, api_key="k")

    assert settings.emulator_failure_mode == "decline"


def test_emulator_failure_mode_error_is_accepted() -> None:
    settings = Settings(
        database_url=DB, rabbitmq_url=MQ, api_key="k", emulator_failure_mode="error"
    )

    assert settings.emulator_failure_mode == "error"


def test_unknown_emulator_failure_mode_fails_at_start_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("RABBITMQ_URL", MQ)
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("EMULATOR_FAILURE_MODE", "explode")

    with pytest.raises(ValidationError):
        load_settings()
