# A Decline is a business outcome, not a Delivery failure

The Emulator's 10% `failed` result sets the Payment to `failed` and sends the Webhook; it is never retried and never reaches the DLQ. Only technical errors (Webhook non-2xx or timeout, DB errors) are retried, otherwise 10% of all payments would end up in the DLQ. A Payment whose status commit fails on every attempt stays `pending` in the DLQ; re-driving it is a documented manual step, and an automatic sweeper is out of scope.

## Emulator failure mode

The task's "10% error" can also be read as a technical error. `EMULATOR_FAILURE_MODE=error` supports that reading: the Emulator's 10% outcome raises `EmulatorError` instead of returning `failed`. The handler's transaction rolls back, so the Payment stays `pending`, no Webhook is sent, and the message goes through the Retry queues (each retry runs the Emulator again) and then to the DLQ with `x-last-error` naming `EmulatorError`. The default stays `decline`: a business outcome the client is told about by Webhook is the more useful behaviour, and with `error` a Payment that fails every attempt is never finalized and never webhooked (re-driving it is the manual step above).
