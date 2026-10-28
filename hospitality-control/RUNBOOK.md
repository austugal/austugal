# Operating Austugal Control

## Start and stop

Run `python run.py --demo` for a loopback-only synthetic demo. Use `python run.py --data private-data` for random initial account credentials. Stop with Ctrl+C. Run the same command and data directory to resume. Data persists independently of the process. Demo and private modes cannot reuse each other's directory accidentally.

The application is standard-library Python. It has no package-install step. The HTTP listener is intended for local evaluation. The optional `npm run dev` launcher starts a private synthetic instance at port 3000 using `dev-data`; it is a development helper, not a deployment platform.

## Account administration

These commands require host filesystem access and are not web endpoints. Run them from the project directory. Replace the sample username with the intended account. Account passwords are generated, never accepted in shell arguments.

```sh
python -m control.admin --data private-data users
python -m control.admin --data private-data account lis-supervisor --property LIS --role manager
python -m control.admin --data private-data account lis-desk --reset-password
python -m control.admin --data private-data account lis-concierge --disable
```

Account changes revoke existing sessions. Generated passwords are printed once; save them privately. Re-enable an existing account by running `account USER --reset-password` without `--disable`. Moving an existing user between properties is rejected: create a separate account instead. Do not give agent accounts to guests.

## Backup and restore rehearsal

```sh
python -m control.admin --data private-data backup backups/rehearsal-001
```

This uses SQLite's backup API and checks database integrity, then copies the matching audit key. The new destination must not already exist. Protect the whole backup directory as sensitive data. A database without its key cannot verify its audit history.

To rehearse restoration:

1. Stop the application using the original directory.
2. Copy the backup directory to a new private location, preserving restrictive permissions.
3. Revoke backed-up sessions before exposing the restored instance, using the command below.
4. Run `python run.py --data RESTORED_DIRECTORY` (include `--demo` only if the backup originated in demo mode).
5. Sign in, inspect a known completed action, then load and verify the audit ledger.
6. Keep the original directory intact until the restored state is accepted.

```sh
python -m control.admin --data RESTORED_DIRECTORY revoke-sessions
```

Test restoration routinely. A backup command succeeding is not by itself a full disaster-recovery plan. The automated suite verifies restoration of a committed checkout change and its audit chain.

## Pause and conflict recovery

A manager can pause writes in **Control room**. Reads and event intake remain available; proposals, approvals, execution, task updates and event processing stop. Resume by clearing the pause and saving. Pausing alone does not change the policy version. Fee or maximum-checkout changes do.

For `stale_reservation`, `policy_changed`, `revoked` or a room conflict, inspect the current state and create a new proposal. Never edit an already-approved payload or force a conflict into an executed state. A timed-out API client should retry with the same idempotency key or proposal ID and inspect the result before creating anything new.

For `dead_letter` events, inspect the room and expected version. Submit a new event only after reconciliation. Processing the queue again does not reapply completed or failed events.

## Optional model configuration

Supply `OPENAI_API_KEY` and `OPENAI_MODEL` via your process environment or secret manager. Both must be set together. The UI will show “configured”, which is not proof of live access. Test the model with synthetic input in your account; verify valid structured output, policy rejection, refusal and network failure. The local parser and manual forms remain available without the model.

API keys are never embedded in the browser. Request text is sent to the provider only when a user selects OpenAI. Do not enter payment data or real guest information during evaluation.

## Remote pilot prerequisites

This release has no hosted deployment. If preparing an authorised private pilot:

- Use private mode, individual accounts and a private network; do not publish demo credentials.
- Put the Python server behind a maintained TLS reverse proxy with request/header/time limits, connection limits and origin restrictions. Bind the backend to a protected interface. Configure `--origin https://YOUR_HOST` to match the browser origin. If the proxy rewrites Host, preserve or explicitly configure the canonical host. HTTP origins are supported for local development only.
- The built-in listener is not a substitute for a hardened production application server. It does not provide SSO, MFA, password recovery email, distributed rate limiting or high availability.
- Add operational alerts, key/secret rotation, retention and deletion procedures, off-host backups and externally retained audit checkpoints.
- Complete independent security and privacy review with the target hosting configuration and guest-data scope.
- Complete real desktop/mobile browser tests and accessibility review. This repository's view-generation unit tests do not prove rendered layout or click behaviour.
- Implement and acceptance-test the live system adapters before promising live hotel functionality. No live PMS or payment adapter exists here.

## Commercial release gate

| Gate | Current status |
| --- | --- |
| Local deterministic workflows | Automated tests pass |
| Persistence, concurrency, replay and rollback | Automated tests pass |
| Property boundaries and role enforcement | Automated tests pass |
| Account management and backup restoration | Automated tests pass |
| Frontend view/request unit tests | Pass; simulated DOM, not a browser |
| Full interactive browser acceptance | Outstanding; environment blocked interactive QA |
| Live model credentials and responses | Outstanding; contract tests use simulated transport |
| Live hotel integration | Not implemented |
| Production deployment and external security review | Outstanding |
| Complete PMS inventory, billing and payment rules | Outside the implemented scope |

These gates describe the actual release. Passing local tests does not establish parity with another product or justify an enterprise-readiness claim.
