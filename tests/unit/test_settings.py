import pytest
from pydantic import ValidationError

from payments.core.settings import Settings

DB = "postgresql+asyncpg://unused@localhost/unused"
MQ = "amqp://unused@localhost/"


def test_default_retry_delays_give_three_attempts() -> None:
    settings = Settings(database_url=DB, rabbitmq_url=MQ, api_key="k")

    assert settings.retry_delays == (1, 2)


@pytest.mark.parametrize("key", ["", "  ", "\t\n"], ids=["empty", "spaces", "tabs"])
def test_empty_or_blank_api_key_fails_at_start_up(key: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=DB, rabbitmq_url=MQ, api_key=key)
