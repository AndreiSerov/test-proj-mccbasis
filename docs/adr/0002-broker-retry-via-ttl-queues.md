# Retries use RabbitMQ TTL queues, not in-process sleeps

On a Delivery failure the consumer acks the message and republishes it to the next Retry queue (by default `payments.new.retry.1s` and `.2s`, one per entry of `RETRY_DELAYS`), whose message TTL and `x-dead-letter-exchange` route it back to `payments.new`; the attempt count travels in a message header. After the last retry fails (the third attempt by default), the message goes to the DLQ (`payments.new.dlq` on `payments.dlx`) with the last error in a header. This keeps the consumer free while messages wait, survives consumer restarts, and makes the retry topology visible in the broker.

## Considered Options

- `asyncio.sleep` with retries inside the handler: simpler, but holds a prefetch slot during backoff and loses the attempt state on restart.
- The `rabbitmq_delayed_message_exchange` plugin: one queue instead of one per delay, but needs a non-default broker plugin in the compose image.

## Consequences

The whole handler is retried, so it must be idempotent: it locks the Payment with `FOR UPDATE`, and if the Payment is already final it skips the Emulator and only re-sends the Webhook. The Webhook is therefore delivered at least once.
