# 02: Webhook URLs cannot reach internal hosts, and are masked in logs

**What to build:** A client can today set `webhook_url` to any host the consumer can reach; in the review it reached the RabbitMQ management API and Postgres (review F2, SSRF). Credentials in the URL (`http://user:pass@host/`) also end up in JSON logs and in the DLQ's `x-last-error` header.

1. **Blocking.** The consumer refuses to deliver a Webhook whose host resolves to a loopback, private, link-local (including `169.254.169.254`), unspecified, multicast or reserved address, IPv4 or IPv6. The check runs in the consumer at send time on every resolved address (a name can resolve to a private address later than the API saw it), and the request goes to the address that was checked, so DNS rebinding between check and connect does not get through. A refused Webhook is an Unprocessable message: straight to the DLQ, no retries, with the reason in `x-last-error`. The API also rejects with `422` a `webhook_url` whose host is a literal blocked IP, or `localhost`, so obvious mistakes fail fast.
2. **Allowlist.** `WEBHOOK_ALLOWED_HOSTS` (comma-separated hostnames or IPs, default empty) skips the check for exactly those hosts. Compose sets it to `webhook-sink` so the demo works; the system tests set it for their local receiver.
3. **Masking.** Every log record and `x-last-error` shows the URL without userinfo, and with the query string replaced by `?…` if it had one; the stored Payment and the GET response keep the URL as given.

Add the terms you need to `GLOSSARY.md` (for example a "Blocked address"), and note the decision in the README design decisions.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] A Payment with `webhook_url` pointing at a host that resolves to a private or loopback address, not allowlisted, ends in the DLQ after one attempt with the reason in `x-last-error`, and no HTTP request reaches that host
- [x] The same URL with its host in `WEBHOOK_ALLOWED_HOSTS` is delivered
- [x] `POST /api/v1/payments` with `webhook_url` `http://127.0.0.1/…`, `http://[::1]/…`, `http://169.254.169.254/…`, `http://10.0.0.1/…` or `http://localhost/…` returns `422`
- [x] With a URL containing `user:secret@` and `?token=x`, neither `secret` nor `token=x` appears in any log line or in `x-last-error`
- [x] The compose demo (`--profile demo`) still delivers to `webhook-sink`
- [x] `scripts/check` passes

## Comments

### 2026-11-11

- Pure rules live in `src/payments/domain/webhook_url.py` (`mask_url`, `is_blocked_address`, `is_allowed_host`, `rejected_literal`); resolution and pinning in `src/payments/consumer/webhook.py`. `mask_url` is the single helper behind every log message and `x-last-error` (the DeliveryFailure and Unprocessable messages are built from it).
- Pinning: the consumer resolves with `loop.getaddrinfo`, refuses if ANY address is blocked, then POSTs to the first checked IP with the original `Host` header; for https it passes `extensions={"sni_hostname": host}`, so SNI and certificate verification still use the name. Done for both schemes. Only the first address is tried (no fallback to the others). Redirects are not followed (httpx default), so a 3xx is a Delivery failure.
- Allowlisted hosts (exact, case-insensitive) skip resolution entirely and use the original URL. A `gaierror` is a DeliveryFailure (retried); a refused host is Unprocessable.
- The API check (`422`) is literal only: `localhost`, `*.localhost` and literal blocked IPs; it honours the allowlist, so a deployment that allowlists `127.0.0.1` can also create such Payments. Names are not resolved in the API.
- Found while testing: httpx itself logs every request URL at INFO, userinfo and query included. The consumer app now sets the `httpx` logger to WARNING.
- Userinfo still works as Basic auth on delivery (httpx behaviour is unchanged).
- Tests: unit `tests/unit/test_webhook_url.py` (masking, address classes, IPv4-mapped, literal rejection) and `tests/unit/test_webhook_delivery.py` (fake resolver + MockTransport: refusal, any-blocked, pinning, SNI, allowlist, DNS failure, masking); system tests in `test_processing.py` (blocked -> DLQ after one attempt with no request, allowlisted delivered, secret/token absent from logs and `x-last-error`) and `test_payments_api.py` (the five 422 URLs). The system tests' API and consumer allowlist `127.0.0.1` by default.
- Criterion 5 checked by hand: `scripts/compose --profile demo up -d --build`, created a Payment to `http://webhook-sink:9000/hook?token=x`, the sink received the Webhook, no `token=x` in the consumer log; then `--profile demo down`.
- `scripts/check`: ruff and pyright clean, 151 passed.
- Not tested: real HTTPS pinning against a live TLS server (only that the `sni_hostname` extension is set on the request); real DNS rebinding.

- Review fixes (2026-11-11): pinning no longer rewrites the URL host to the IP, because httpcore pools by origin and would reuse a TLS connection verified for one name for another name on the same IP. The URL keeps the hostname; `send_webhook` resolves and checks, then sets a context variable `(hostname, checked IP)` that `_PinningBackend.connect_tcp` (installed by `PinningTransport`, which the consumer's client uses) reads to connect to the IP. Pooling, `Host`, SNI and certificate verification all use the name. The allowlist path sets no pin. Unit tests now assert the connect target, the TLS `server_hostname`, and that two names on one IP do not share a connection. Error detail is only the exception type name; an `httpx.InvalidURL` is Unprocessable with a masked message; `PaymentHandler(webhook_allowed_hosts=...)` is required. `PinningTransport` replaces the httpx transport's private `_pool` (httpx has no hook for the network backend). Not tested: a real TLS server.
