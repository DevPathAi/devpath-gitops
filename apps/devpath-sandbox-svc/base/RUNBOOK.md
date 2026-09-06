# Sandbox runner hardening rollout gate

This base is intentionally fail-closed. `devpath-sandbox-svc` is not Ready until
all of the following externally provisioned prerequisites exist:

1. A dedicated runner workload labelled `app=devpath-sandbox-runner` listens on
   port 2376, verifies client certificates, and has the `runsc` runtime installed.
   The application pod never mounts `/var/run/docker.sock` or a `hostPath`.
   **Provisioned 2026-08-22**: `apps/devpath-sandbox-runner/base` (DinD +
   pinned gVisor 20250820.0, mTLS-only 2376 — the dind entrypoint is bypassed
   because it injects an unencrypted 2375 listener, measured). The runner
   Service is owned by that app, not this base.
2. Secret `sandbox-runner-mtls` contains the Docker client CA, certificate, and
   key expected by docker-java (`ca.pem`, `cert.pem`, `key.pem`).
   **Provisioned 2026-08-22** alongside `sandbox-runner-server-tls` (server
   side) and `sandbox-runner-ca` (rotation material; CA private key lives only
   in this cluster secret — local copies were shredded).
3. Secret `devpath-internal-auth` contains key `sandbox-token`. AI and LCS
   clients must send it as `X-DevPath-Internal-Token` before the sandbox image is
   rolled out; merely injecting the environment variable is not sufficient.
   **Provisioned 2026-08-22**; ai-svc and lcs-svc deployments already inject it.

Verification evidence (2026-08-22): runner engine registers runsc
(`docker info` Runtimes), a runsc container reports the gVisor synthetic kernel
(`Linux version 4.4.0 … 2016`), a certificate-less client is rejected, and a
canary of the released sandbox image (bf9ad1a…) with this base's full hardened
environment reached readiness UP against the live runner (readiness group
includes `sandboxRunner`).

Runner restart gate (2026-08-23): `/var/lib/docker` is intentionally ephemeral,
so the runner preloads the exact runtime image tags used by `sandbox-svc`
(`eclipse-temurin:21-jdk`, `node:20-alpine`, and `python:3.12-slim`) after every
start. Readiness remains false until all three images are locally inspectable;
do not replace this with a TCP-only probe. The Unix Docker socket is internal to
the privileged runner pod and is never exposed through a Service, volume, or
application-pod mount. External runner traffic remains mTLS-only on 2376.

Service/runtime binding (2026-08-23): production uses source commit
`649bef299dd10a37e94647b8cd6fb1eaaaea6267` (published OCI digest
`sha256:966af62fa62c46a3552ab2a7d4ee39eadd62324e3716952b164e2450a80bc205`).
This release binds `SANDBOX_RUNNER_TLS_VERIFY` into the Docker client and moves
submitted source into a runner-owned labelled volume before starting the
read-only runsc container. Because this gVisor release fails sandbox creation
when Docker cgroup `PidsLimit` is set, process limits are enforced inside runsc
with `nproc` ulimits (128 for execution and 16 for source-loader containers).
The prior `bf9ad1a…` image predates both remote-runner contracts and must not be
restored as an operational rollback target.
The immutable image runs as UID/GID `100:101`; the pod `fsGroup` must remain
`101` and the projected mTLS Secret mode must remain `0440` so the non-root app
can read the client key without making it world-readable.

Required order:

1. Preserve the exact ET8 `devpath-shared` checkpoint
   `2b03c38934fdd19332da59107e4330a3af92d078` through `V202608161008`, then
   preserve the prior final lineage
   `58c78bfe35e99e618863b53f689c216b40295826` through `V202608201002`, then
   publish `2fda29d38bc94345aa91bb6ea5823aef8125b0dc` and run its migration image
   through `V202609051004`. Do not edit the already exercised
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
     # Re-measure immediately before approval. 2026-09-06 reference: 0 rows,
     # 24,576 total bytes, zero relation locks, zero non-idle client sessions.
     max-support-requests-rows: "<approved integer>"
     max-support-requests-bytes: "<approved integer>"
   ```

   Scale Sandbox writers down first. The migration Job fails closed unless
   duplicate active users are zero, `pg_stat_activity` has no other active
   client traffic, both affected tables are within their approved row/size
   bounds, and `ACCESS EXCLUSIVE NOWAIT` lock rehearsals for `sandbox_sessions`
   and `support_requests` succeed under 2-second lock and 30-second statement
   timeouts. V202608161001 performs a data scan in the same transaction as its
   first `ALTER TABLE`; V202609051001 validates existing support rows while its
   ALTER is held. The maintenance gate bounds both risks but does not make
   either migration low-lock.
3. Verify the final schema before any application rollout:

   ```sql
   SELECT version, success
   FROM flyway_schema_history
   WHERE version IN (
     '202608161008', '202608201002',
     '202609051001', '202609051002', '202609051003', '202609051004'
   )
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
