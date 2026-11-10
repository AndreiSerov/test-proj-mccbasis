# A Decline is a business outcome, not a Delivery failure

The Emulator's 10% `failed` result sets the Payment to `failed` and sends the Webhook; it is never retried and never reaches the DLQ. Only technical errors (Webhook non-2xx or timeout, DB errors) are retried, otherwise 10% of all payments would end up in the DLQ. A Payment whose status commit fails on every attempt stays `pending` in the DLQ; re-driving it is a documented manual step, and an automatic sweeper is out of scope.
