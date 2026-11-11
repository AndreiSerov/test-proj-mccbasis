# 02: Webhook URLs cannot reach internal hosts, and are masked in logs

**What to build:** A client can today set `webhook_url` to any host the consumer can reach; in the review it reached the RabbitMQ management API and Postgres (review F2, SSRF). Credentials in the URL (`http://user:pass@host/`) also end up in JSON logs and in the DLQ's `x-last-error` header.

1. **Blocking.** The consumer refuses to deliver a Webhook whose host resolves to a loopback, private, link-local (including `169.254.169.254`), unspecified, multicast or reserved address, IPv4 or IPv6. The check runs in the consumer at send time on every resolved address (a name can resolve to a private address later than the API saw it), and the request goes to the address that was checked, so DNS rebinding between check and connect does not get through. A refused Webhook is an Unprocessable message: straight to the DLQ, no retries, with the reason in `x-last-error`. The API also rejects with `422` a `webhook_url` whose host is a literal blocked IP, or `localhost`, so obvious mistakes fail fast.
2. **Allowlist.** `WEBHOOK_ALLOWED_HOSTS` (comma-separated hostnames or IPs, default empty) skips the check for exactly those hosts. Compose sets it to `webhook-sink` so the demo works; the system tests set it for their local receiver.
3. **Masking.** Every log record and `x-last-error` shows the URL without userinfo, and with the query string replaced by `?…` if it had one; the stored Payment and the GET response keep the URL as given.

Add the terms you need to `GLOSSARY.md` (for example a "Blocked address"), and note the decision in the README design decisions.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] A Payment with `webhook_url` pointing at a host that resolves to a private or loopback address, not allowlisted, ends in the DLQ after one attempt with the reason in `x-last-error`, and no HTTP request reaches that host
- [ ] The same URL with its host in `WEBHOOK_ALLOWED_HOSTS` is delivered
- [ ] `POST /api/v1/payments` with `webhook_url` `http://127.0.0.1/…`, `http://[::1]/…`, `http://169.254.169.254/…`, `http://10.0.0.1/…` or `http://localhost/…` returns `422`
- [ ] With a URL containing `user:secret@` and `?token=x`, neither `secret` nor `token=x` appears in any log line or in `x-last-error`
- [ ] The compose demo (`--profile demo`) still delivers to `webhook-sink`
- [ ] `scripts/check` passes
