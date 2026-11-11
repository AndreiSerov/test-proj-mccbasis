# Payments

An async payment processing service: it accepts payment requests, processes them through an emulated gateway, and notifies the client by webhook.

## Language

**Payment**:
A client's request to charge an amount in one currency, with a lifecycle `pending → succeeded | failed`.
_Avoid_: Transaction, order, charge

**Idempotency key**:
The client-supplied `Idempotency-Key` header value that identifies one logical payment request; the same key with the same body returns the existing Payment, with a different body returns 409.
_Avoid_: Request ID, dedup key

**Request hash**:
A hash of the normalized create-payment body, stored with the Payment to detect an Idempotency key reused with a different body.

**Outbox event**:
A row in the `outbox` table written in the same transaction as the Payment, describing an event (`payments.new`) still to be published to the broker.
_Avoid_: Outbox message, job

**Relay**:
The asyncio task inside the consumer process that publishes unpublished Outbox events to RabbitMQ and marks them `published_at`.
_Avoid_: Outbox worker, publisher, poller

**Emulator**:
The stand-in for the external payment gateway: waits 2–5 s and succeeds 90% of the time.
_Avoid_: Gateway, processor

**Decline**:
An Emulator result of `failed`; a normal business outcome that sets the Payment to `failed` and is never retried.
_Avoid_: Error, failure (for this meaning)

**Delivery failure**:
A technical error while handling a message (webhook non-2xx or timeout, DB error) that triggers a retry.

**Retry queue**:
One of the TTL queues `payments.new.retry.{1s,2s}` (by default; one per entry of `RETRY_DELAYS`) that hold a message for its delay, then dead-letter it back to `payments.new`.
_Avoid_: Delay queue, backoff queue

**Unprocessable message**:
A message that can never be processed (malformed body, unknown Payment, a Webhook host that resolves to a Blocked address); it goes straight to the DLQ without retries.

**Blocked address**:
An IP address a Webhook must never be sent to: loopback, private, link-local (including `169.254.169.254`), unspecified, multicast or reserved, IPv4 or IPv6 (an IPv4-mapped IPv6 address counts as its IPv4). The consumer refuses a Webhook whose host resolves to any Blocked address, unless the host is in the Allowed hosts.
_Avoid_: Internal IP, forbidden address

**Allowed hosts**:
The hostnames or IPs in `WEBHOOK_ALLOWED_HOSTS` (exact, case-insensitive match) whose Webhooks skip the Blocked address check.
_Avoid_: Whitelist

**DLQ**:
The `payments.new.dlq` queue that holds messages still failing after 3 attempts (the first and two retries), and messages that can never be processed (malformed body, unknown Payment), with the last error in a header. Messages get in two ways: the consumer republishes them to `payments.dlx` (carrying `x-attempt` and `x-last-error`), or the broker dead-letters one that `payments.new` rejected without requeue (no such headers).
_Avoid_: Dead queue, error queue

**Webhook**:
The HTTP POST to the Payment's `webhook_url` reporting its final status; delivered at least once.
_Avoid_: Callback, notification
