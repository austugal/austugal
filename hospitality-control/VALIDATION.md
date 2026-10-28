# Validation record — 28 October 2026

## Executed locally

- **54 Python tests**: core workflows, real local HTTP API requests, concurrency, replay, persistence, property boundaries, role enforcement, policy updates, expiry, revoked identities, event failure/recovery, audit alteration detection, transaction rollback, account administration and backup restoration.
- **10 JavaScript unit tests**: all six views across four roles, output escaping, role-specific action controls, self-review suppression, request-key generation, stale-response rejection, retry-key retention, session-cache cleanup and currency formatting. These run in a simulated DOM using Node's VM; they do not launch a browser.
- JavaScript syntax check.
- Python module compilation.

The tests create isolated temporary databases and synthetic records. Model provider tests inject a simulated transport and check request/response handling, timeout, incomplete responses and policy rejection. No live model request or live PMS call is counted as passing.

## Browser verification limit

The login page was observed in the provided cloud browser. Further interactive QA was blocked by that environment's browser security policy. No successful browser end-to-end flow, mobile rendering check, screenshot review or accessibility acceptance is claimed. These remain release gates in `RUNBOOK.md`.

## CI

The repository includes a GitHub Actions matrix for Python 3.12/3.13 on Linux and Windows, with Node 22 for frontend tests. The workflow is the reproducible check; local results do not imply an unobserved remote run has passed.

## Acceptance evidence

| Requirement | Test evidence |
| --- | --- |
| Approval is separate from execution | Reservation remains unchanged after approval |
| No self-approval or agent execution | Forbidden operations fail server-side |
| Persistent changes | Restarted store retains committed checkout |
| Exactly one local execution under concurrency | Eight simultaneous calls yield one change and seven replay results |
| Conflicting agents cannot overwrite | Second proposal fails its reservation-version check |
| One suite cannot be assigned twice | Second reservation fails the current occupancy check |
| State is rechecked | Room-readiness change blocks an earlier approved upgrade |
| Superseded policy cannot execute | Fee change invalidates the earlier policy version |
| Pause is reversible | Execution blocked while paused, then succeeds after resume if still valid |
| Atomic local transaction | Injected audit-write failure rolls back the hotel change |
| Event recovery | Deduplication, ordered execution, dead-letter state and corrected event verified |
| Scoped data access | Other-property proposal access fails; agent names and unrelated proposals omitted |
| Authentication lifecycle | Logout, expiry, account disablement and password rotation revoke access |
| Audit integrity | Altered signed record fails chain verification |
| Backup restoration | Restored committed state has a valid matching audit chain |
| Model cannot grant authority | Invalid model action is rejected by deterministic policy |

## Not established

External PMS compatibility, production scalability, hosted availability, commercial product parity, legal/regulatory compliance and resilience to a fully compromised host are not established by these tests. Do not reinterpret a local success as any of those claims.
