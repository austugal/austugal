# HTTP API

Base URL for the local demo: `http://127.0.0.1:8080/api`.

Send JSON with `Content-Type: application/json`. Except for health, bootstrap and login, every endpoint requires `Authorization: Bearer YOUR_SESSION_TOKEN`. Login sessions expire after one hour. Property and role are resolved from that session; client-supplied authority fields are rejected.

Errors use `{"error":{"code":"...","message":"..."}}`. Relevant HTTP statuses: 400 validation; 401 authentication; 403 permission; 404 missing scoped record; 409 state/policy conflict; 413 oversized request; 415 content type; 423 paused; 429 throttled; 502 model provider failure; 503 provider unconfigured or storage unavailable.

## Routes

| Method | Path | Access / purpose |
| --- | --- | --- |
| GET | `/health` | Public process health, no hotel data |
| GET | `/bootstrap` | Public demo-mode and provider-configured flags |
| POST | `/login` | Username/password; returns token and expiry |
| POST | `/logout` | Current session; body `{}` |
| GET | `/state` | Property-scoped state, role-filtered |
| POST | `/assistant` | Draft only; `message`, `provider` (`local` or `openai`) |
| POST | `/proposals` | Scoped staff/agent action proposal |
| POST | `/proposals/{id}/review` | Manager; `decision` and nonempty `reason` |
| POST | `/proposals/{id}/execute` | Manager; body `{}` |
| POST | `/tasks/{id}` | Staff/manager; `status`, expected `version` |
| POST | `/events` | Staff/manager; durable room-readiness event intake |
| POST | `/events/process` | Manager; body `{}`; up to 100 queued events |
| POST | `/policy` | Manager; full validated policy with expected version |
| GET | `/audit` | Manager; signed records and verification result |

The maximum JSON request body is 16 KiB. Unknown fields are rejected. Do not send credentials or tokens in URLs. There is no CORS-enabled cross-origin browser API.

## Proposal examples

Late checkout:

```json
{"kind":"late_checkout","reservation_id":"R1001","payload":{"hour":14},"idempotency_key":"client-operation-001"}
```

Complimentary room upgrade:

```json
{"kind":"room_upgrade","reservation_id":"R1001","payload":{"room":"201"},"idempotency_key":"client-operation-002"}
```

Service task:

```json
{"kind":"service_task","reservation_id":"R1002","payload":{"category":"housekeeping","description":"Bring two towels"},"idempotency_key":"client-operation-003"}
```

Successful creation returns `id`, `preview` and `replayed:false`. A duplicate request with the same key and input returns the original ID and `replayed:true`. Different input with that key returns `idempotency_conflict`.

Approve using a different manager account:

```json
{"decision":"approve","reason":"Verified request and current hotel policy"}
```

Reject using `"decision":"reject"`. Approval does not execute. Execute separately with `{}`. Successful execution returns a stored result and replay flag. All state/permission checks still apply at execution.

## Tasks

Allowed transitions are `open → in_progress → done`. Supply the task version from current state:

```json
{"status":"in_progress","version":1}
```

A stale version fails. Direct `open → done` fails. Agents cannot manage tasks.

## Room events

`version` is the expected room version from `/state`, not a vendor event sequence:

```json
{"external_id":"housekeeping-001","room":"202","ready":true,"version":1}
```

Event intake returns an ID. Processing is an explicit manager call. Inspect `/state` for `queued`, `processed` or `dead_letter`, with a reason on failure. Event IDs deduplicate per property.

## Policy

`checkout_fee` is an integer in currency minor units per hour after 11:00; it is a quote, not a financial posting. `version` is the current policy version:

```json
{"paused":false,"max_checkout":16,"checkout_fee":2500,"version":1}
```

A material policy change increments its version and invalidates existing proposals at execution. Stale policy edits return `stale_policy`. Pause/resume alone does not increment the policy version.

## Audit

`GET /audit` returns `valid`, `entries` and a signed `head` for a valid chain, or `broken_at` on detected corruption. The export itself is recorded. Store head signatures outside the application's host if independent checkpoint evidence is required. Entries avoid guest names and task description text.
