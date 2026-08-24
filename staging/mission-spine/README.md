# Mission Spine staging stack

This overlay reuses the production workload shapes but excludes every production
`SealedSecret`. It is rendered with the repository as its trust root:

```sh
kubectl kustomize staging/mission-spine --load-restrictor LoadRestrictionsNone
```

Before applying it, provision these namespace-local secrets without committing
their values: `platform-db`, `devpath-jwt`, `mission-spine-release-control`,
`ai-claude`, `sandbox-runner-server-tls`, and `sandbox-runner-mtls`. Copy the
already-issued production `devpath-web-tls` and `devpath-gateway-tls` TLS assets
into `devpath-staging`; they terminate only candidate-header routes. Apply
`kafka/staging-cluster.yaml` first so staging events never enter production
topics.

The three `*.staging.leva.ai.kr` hosts must resolve directly to the k3s public IP
before cert-manager can issue `mission-spine-release-hosts-tls`. The service
images are protected-main source-SHA tags; the web image remains managed by the
candidate staging workflow.
