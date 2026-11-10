from pamqp.commands import Basic


class PublishNotConfirmed(Exception):
    """The broker did not positively confirm a publish (nack or unroutable return)."""


def require_ack(confirmation: object, what: str) -> None:
    """Raise unless the broker answered a publish with a positive `Basic.Ack`."""
    if not isinstance(confirmation, Basic.Ack):
        raise PublishNotConfirmed(f"{what}: broker answered {confirmation!r}")
