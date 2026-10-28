# Austugal Control architecture

## Scope and ownership

Austugal Control is an independently implemented hotel workflow application. Its code and product documentation describe its own functionality. The bundled database is a synthetic hotel backend, not a replica of a third-party PMS contract.

## Components

- `control/server.py`: HTTP routing, bearer authentication, origin/host validation, request limits and security headers.
- `control/store.py`: SQLite state, identity checks, policy checks, proposal lifecycle, deterministic action execution, events and audit signatures.
- `control/assistant.py`: local command parsing and optional structured model output. Neither path has approval or execution authority.
- `control/admin.py`: host-operator account administration and consistent backups.
- `static/`: browser UI. Tokens are held in memory, not local storage. Reloading requires signing in again.

No long-running model agents are started automatically. External software can authenticate using a scoped agent account and use the API. Its permissions are enforced by the server, not by a prompt.

## Action lifecycle

A proposal records its actor, property, exact payload, reservation version, policy version, creation time, expiry and idempotency key. It begins pending. A different manager can approve or reject it, with a reason. Rejection is terminal. The execution endpoint accepts only the proposal ID; it cannot substitute a new payload.

Inside one `BEGIN IMMEDIATE` transaction, execution:

1. Authenticates the current executor as a property manager.
2. Returns the previous result if already executed.
3. Requires the approved state and checks the emergency pause.
4. Revalidates expiry, the proposing account and its current role, and the reviewing manager's access.
5. Requires the original policy version and reservation version.
6. Checks current stay status, action limits and any suite's current readiness and occupancy.
7. Changes the local record or creates the task, stores the result and appends the audit entry atomically.

A stale reservation, superseded policy or revoked authority terminates the proposal as a conflict. Expiry terminates it as expired. Pause is retryable: the approved proposal remains approved, but expiry still applies. Storage failure rolls back both the business change and audit write. No successful response is emitted for an uncommitted operation.

The transaction boundary covers only the local SQLite database. **It cannot provide atomicity across external APIs.**

## Idempotency contract

`idempotency_key` is scoped to the property and proposing account. Reusing it with identical input returns the original proposal ID. Reusing it with different input returns HTTP 409. Different keys mean different requests. Execution is keyed by proposal ID. A repeated execution returns the committed result without a second state change.

A response lost after commit can safely be recovered by retrying the same key or proposal ID. Browser submission retains its key after a failed request while that page remains open. A browser reload discards that in-memory key: inspect the queue before submitting again. A production API client should persist its own operation keys.

## Events

Room-readiness events enter a durable inbox under a unique property-scoped external ID. A manager explicitly processes a batch of up to 100 in insertion order. No background worker is claimed. Each event uses the expected room version. A mismatch becomes `dead_letter`; successful processing increments the version. Duplicate intake cannot repeat the change.

Recovery is deliberate: inspect the current room state, reconcile the discrepancy, then submit a corrected event with a new external ID and the current version. Do not blindly replay a stale event.

## Security and data handling

The authenticated account supplies the property and role. Request bodies never supply authority. The server re-reads account access on each operation; changing a password, role or account status via the host CLI revokes existing sessions. Manager-level auditing and policy changes are enforced server-side. Agent identities have property-wide access to limited reservation facts; they are **not** guest login identities and must not be exposed directly to hotel guests.

The browser escapes user-controlled strings and uses a self-only content security policy. The server restricts static file paths and JSON body size, bounds concurrent request threads and does not emit guest text or tokens in access logs. Host and Origin checks reduce cross-site request and DNS-rebinding exposure. Bearer tokens are not cookies. Remote service deployment requires TLS and a hardened reverse proxy.

Audit details avoid task descriptions and guest names. This is not a complete personal-data classification system. Task descriptions and review reasons remain local records and must follow the operator's retention policy. Passwords, audit keys, database files and environment files are ignored by Git.

## Audit assurance

Each property has an HMAC-linked event chain. The key is stored separately from the SQLite file. Verification detects modifications and interior deletions while the key remains protected. It does not prove who controlled the host, detect deletion of the final chain entries by itself, or resist compromise of both database and key. Independently retain exported head signatures for stronger evidence. The application never silently creates a replacement key for an existing database.

## External integration boundary

No live OPERA/OHIP adapter is included in this release. There are no guessed endpoints, simulated “connected” indicators or production write claims.

A future live adapter must be implemented against the target environment's actual API contract and tested with authorised nonproduction access. It needs separate read and execution credentials, request correlation, rate-limit handling, outcome reconciliation after timeouts, persistent operation tracking, and a policy for partial failure. The current local transaction must not be wrapped around an HTTP call and advertised as end-to-end atomicity.

Likewise, a complete hotel inventory engine needs stay dates, room-type availability, future arrivals, allocation constraints and pricing rules. This release intentionally models only the documented synthetic current-stay workflows.
