import hashlib
import json

from payments.domain.create_payment import CreatePayment


def request_hash(body: CreatePayment) -> str:
    """SHA-256 of a canonical JSON of the validated create-Payment body."""
    canonical = json.dumps(
        {
            "amount": f"{body.amount:.2f}",
            "currency": body.currency.value,
            "description": body.description,
            "metadata": body.metadata,
            "webhook_url": str(body.webhook_url),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
