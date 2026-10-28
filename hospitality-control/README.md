# Austugal Control

A working local application for coordinating hotel requests, approvals and execution. It includes a Python HTTP API, persistent SQLite storage and a responsive operator interface. No Python packages, frontend framework, cloud account or AI key are required to run the included workflows.

**Version 1.0 — local workflow engine, synthetic hotel backend.** This version changes its own hotel database. It does not connect to a live PMS, collect payments or claim production certification. The optional OpenAI planner is contract-tested with a simulated transport; live provider access has not been verified.

## Start in one command

Requires Python 3.12 or newer. From this directory:

```sh
python run.py --demo
```

Open **http://127.0.0.1:8080**. The sign-in screen offers demo accounts. All demo passwords are `Austugal-demo-2026`. Demo mode binds only to loopback.

| Account | Role | Property |
| --- | --- | --- |
| `lis-desk` | Front desk | Lisboa |
| `lis-manager` | Manager | Lisboa |
| `lis-concierge` | Concierge agent identity | Lisboa |
| `lis-upsell` | Upsell agent identity | Lisboa |
| `mel-manager` | Manager | Melbourne |

The “agent” accounts are scoped API identities, not autonomous processes running in the background.

## Complete a real workflow

1. Sign in as **Front desk**. Open **Requests & approvals**.
2. Select reservation `R1001`, late checkout and `14:00`. Submit it. The reservation remains at `11:00`.
3. Sign out and sign in as **Manager**. Review the request and enter an approval reason.
4. Click **Recheck & execute**. The reservation changes to `14:00`, its version increments and a signed audit entry is committed in the same transaction.
5. Stop and restart the server. The new checkout time remains saved.

To prove conflict protection, submit two requests for the same reservation before executing either. Approve both. Execute one. The other fails with `stale_reservation` instead of overwriting the first change.

Additional working paths:

- Move a classic-room stay to a ready, vacant suite. Competing reservations cannot acquire the same suite. The old room becomes dirty.
- Create a housekeeping or maintenance task, start it and complete it.
- Queue room-readiness events, process them, inspect stale events in the failure queue and submit a corrected event with the current room version.
- Pause writes, resume them, change checkout policy and verify that old approvals cannot use a superseded policy.
- Export and verify the property's signed audit ledger.
- Use the assistant to draft actions or retrieve the current property policy. Local commands need no model or network access.

## What is implemented

| Capability | Behaviour |
| --- | --- |
| Authentication | PBKDF2 password hashing, hashed bearer sessions, one-hour session lifetime, persistent login throttling and immediate account revocation checks |
| Property boundaries | Every operational query and change is scoped by the authenticated account; body fields cannot select another property or impersonate a role |
| Agent permissions | Concierge can propose checkout and service tasks; upsell can propose suite moves; agent views omit guest names and expose only their own proposals |
| Independent review | A manager cannot approve their own proposal; approval and execution are distinct steps |
| Guarded execution | Rechecks proposer and reviewer access, policy version, proposal expiry, reservation version and current room availability |
| Duplicate protection | Proposal idempotency keys, execution replay, event deduplication and unique task-per-proposal constraints |
| Concurrency | Serialized SQLite transactions, reservation versions, room versions and a unique occupied-room constraint |
| Reliability | Durable event intake, ordered processing, failure isolation, explicit dead-letter outcomes and transactional rollback |
| Audit | Property-scoped HMAC chains, read/action/denial records, verification and JSON export |
| Administration | Host CLI for account creation, disablement, password rotation and verified SQLite backups |
| Interface | Overview, request queue, review dialog, reservations, tasks, events, assistant, policy controls and audit views |

The late-checkout quote is informational: hourly amount after 11:00 in the property's currency. It does **not** post or collect a charge. Suite upgrades in this version are complimentary. Room allocation is a simplified current-stay model; it does not implement future availability, departure-date inventory or a complete PMS.

## Local assistant and optional AI

Included local commands:

```text
late checkout R1001 to 14
upgrade R1001 to room 201
housekeeping R1001: Bring two towels
maintenance R1002: Inspect the bedside lamp
show policy
```

Local mode is a deterministic command parser. Unrecognised or ambiguous commands produce no action. Drafting never creates an approval or changes a hotel record.

To enable the optional language-model planner, set `OPENAI_API_KEY` and `OPENAI_MODEL` in the server environment to a key and model available to your account, then restart. Never put the key in a URL, browser code or repository file. Select **OpenAI** in the assistant explicitly. Only the entered request text is sent; the application does not attach guest records. Entered text may itself contain personal data, so use synthetic identifiers during evaluation.

The planner requests structured output from the Responses API. All returned action fields pass the same server validation as manual requests. Refusal, timeout, malformed output and policy violations cannot execute an action. Remote planning is limited to 20 attempts per account per five minutes. There is no automatic provider retry or fallback that silently changes the meaning of a request.

## Private local instance

Use a separate directory without `--demo`:

```sh
python run.py --data private-data
```

The first launch creates random account passwords and prints them once. Save them privately. Subsequent launches do not reset accounts. The dataset remains synthetic even in private mode. Do not expose demo mode or its public passwords on the internet.

For operations, account recovery and a restore rehearsal, see [RUNBOOK.md](RUNBOOK.md). For integration boundaries and execution semantics, see [ARCHITECTURE.md](ARCHITECTURE.md). For the API, see [API.md](API.md).

## Verify

```sh
python -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
node --check static/app.js
```

Node 20+ is needed only for JavaScript unit tests; the running application has no Node dependency. No package installation is necessary.

The verification record and outstanding checks are in [VALIDATION.md](VALIDATION.md). Browser view unit tests are not a substitute for a real browser test.

## Release boundary

This release is suitable for local evaluation of the implemented workflows. Before selling a hosted service connected to real hotels, complete live integration acceptance, browser/device acceptance, deployment hardening, independent security review, operational monitoring, retention controls and the hotel-specific policy review described in the runbook. No blanket “all systems supported” or “production ready” claim is made.

Copyright © 2026 Tiago Fitas / Austugal. All rights reserved. See [LICENSE](LICENSE).
