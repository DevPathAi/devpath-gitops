# Sandbox runner hardening rollout gate

This base is intentionally fail-closed. `devpath-sandbox-svc` is not Ready until
all of the following externally provisioned prerequisites exist:

1. A dedicated runner workload labelled `app=devpath-sandbox-runner` listens on
   port 2376, verifies client certificates, and has the `runsc` runtime installed.
   The application pod never mounts `/var/run/docker.sock` or a `hostPath`.
2. Secret `sandbox-runner-mtls` contains the Docker client CA, certificate, and
   key expected by docker-java.
3. Secret `devpath-internal-auth` contains key `sandbox-token`. AI and LCS
   clients must send it as `X-DevPath-Internal-Token` before the sandbox image is
   rolled out; merely injecting the environment variable is not sufficient.

Required order:

1. Preserve the exact ET8 `devpath-shared` checkpoint
   `2b03c38934fdd19332da59107e4330a3af92d078` through `V202608161008`, then
   publish the exact final lineage
   `d3cf41faf21d00b815b398a7492af5506390151a` and run its migration image
   through `V202608161011`. Do not edit the already exercised
   `V202608161001__sandbox_execution_leases.sql` bytes; the deployment preflight
   fails unless both immutable checkpoints are named exactly.
2. Create `sandbox-migration-gate` only for the approved maintenance window:

   ```yaml
   apiVersion: v1
   kind: ConfigMap
   metadata:
     name: sandbox-migration-gate
   data:
     maintenance-approved: "true"
     # Set from the reviewed production table-size preflight, never by guessing.
     max-sandbox-sessions-bytes: "<approved integer>"
   ```

   Scale Sandbox writers down first. The migration Job fails closed unless
   duplicate active users are zero, `pg_stat_activity` has no other active
   client traffic, the table is within the approved size bound, and an
   `ACCESS EXCLUSIVE NOWAIT` lock rehearsal succeeds under 2-second lock and
   30-second statement timeouts. V202608161001 performs a data scan in the same
   transaction as its first `ALTER TABLE`, so this maintenance gate mitigates
   but does not make that historical migration low-lock.
3. Verify the final schema before any application rollout:

   ```sql
   SELECT version, success
   FROM flyway_schema_history
   WHERE version IN ('202608161008', '202608161011')
   ORDER BY version;

   SELECT column_name
   FROM information_schema.columns
   WHERE table_name = 'sandbox_sessions'
     AND column_name IN (
       'owner_instance', 'lease_expires_at', 'reconciliation_token',
       'reconciliation_started_at', 'terminal_source'
     );

   SELECT conname, convalidated
   FROM pg_constraint
   WHERE conname IN (
     'chk_sandbox_status', 'chk_sandbox_terminal_source'
   );

   SELECT indexname, indexdef
   FROM pg_indexes
   WHERE indexname IN (
     'uq_sandbox_one_active_user', 'idx_sandbox_active_lease',
     'idx_sandbox_active_legacy', 'uq_outbox_dedupe_key'
   );
   ```

4. Publish the immutable shared Java package used by sandbox CI.
5. Provision runner mTLS, the internal token, and the runsc runner workload.
6. Deploy AI/LCS client header support and verify internal reads.
7. Deploy sandbox with `maxSurge: 0`; wait for runner-aware readiness.

## Shutdown and stale-session release gates

- Rendered budgets must remain strictly ordered: active remote cancellation at
  60 seconds, executor completion at 75 seconds, Spring phase at 90 seconds,
  and Pod termination grace at 120 seconds.
- Rehearse a real 75-second drain with setup, running, log-drain, and queued
  sessions. Every accepted row must finish with one immutable terminal outbox
  event before the process exits.
- `sandbox.runs.expired_active` counts active rows whose durable lease has
  expired. Wire a sustained alert equivalent to
  `sandbox_runs_expired_active > 0 for 2m` and verify the metric at
  `/actuator/metrics/sandbox.runs.expired_active`.
- The implemented 35-second bound is specifically **lease expiry to terminal**:
  a worst-case 5-second scheduler wait plus 30-second exact-result correction
  window. A live 40-second run with a valid renewed lease is not stale. The
  broader QA wording “accepted wall-clock <=35 seconds” is not equivalent and
  remains an explicit release decision rather than a satisfied claim.

Local manifest evidence:

```powershell
./scripts/verify-sandbox-hardening.ps1
kubectl kustomize apps/devpath-sandbox-svc/base |
  kubectl apply --dry-run=client -f -
```

The server-side dry run and live readiness check require cluster credentials and
the two secrets above. Do not bypass readiness or change `runsc` to `runc` as a
rollout workaround.
