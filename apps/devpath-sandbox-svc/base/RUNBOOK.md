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

1. Publish and run the `devpath-shared` migration image through
   `V202608161002`, then verify both partial active-run indexes.
2. Publish the immutable shared Java package used by sandbox CI.
3. Provision runner mTLS, the internal token, and the runsc runner workload.
4. Deploy AI/LCS client header support and verify internal reads.
5. Deploy sandbox with `maxSurge: 0`; wait for runner-aware readiness.

Local manifest evidence:

```powershell
./scripts/verify-sandbox-hardening.ps1
kubectl kustomize apps/devpath-sandbox-svc/base |
  kubectl apply --dry-run=client -f -
```

The server-side dry run and live readiness check require cluster credentials and
the two secrets above. Do not bypass readiness or change `runsc` to `runc` as a
rollout workaround.
