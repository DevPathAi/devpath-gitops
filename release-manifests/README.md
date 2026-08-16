# Mission Spine release control

Mission Spine releases use two immutable JSON documents. This avoids a self-referential hash: browser journeys cannot produce evidence hashes until they have consumed the release inputs.

## Documents

1. `candidates/<release_id>.candidate-spec.json` is the canonical execution input. It contains only immutable source/image/config/environment inputs and `journey_harness`; it contains no journey output.
2. `releases/<release_id>.json` is the final release manifest. It binds the exact candidate-spec SHA-256 and the Home dist, privacy approval, AI release evaluation, two browser journeys, visual evidence, and accessibility evidence. Every evidence reference repeats `candidate_spec_sha256`.

Both documents conform to `schema-v1.json`. `release_id` must match `^ms-[0-9]{8}-[a-z0-9][a-z0-9-]{2,40}$`. Promotion workflows accept only that ID. A digest, manifest path, branch, cluster, or Cloudflare deployment cannot be supplied as a workflow input. A candidate branch must descend from the exact `gitops.base_sha` and differ by only its canonical candidate file; after validation it must differ by exactly that candidate plus its canonical final manifest. Every production lane rechecks this branch shape before consuming secrets or mutating state.

No production candidate is checked in by this change because the real immutable images, successful workflow artifact IDs, model evaluation, browser journeys, and approvals do not yet exist. The synthetic files under `tests/release/fixtures/` are contract tests only and cannot be resolved by a release workflow.

## Candidate-spec contract

The candidate-spec binds:

- GitOps repository and exact `base_sha`, prior web digest, and the one mutable path `apps/devpath-web/base/kustomization.yaml`;
- source SHA plus immutable OCI digest for every application service;
- Shared source, migration image, Flyway target `202608161011`, required V1011 validation, and the additive-retained rollback policy;
- frontend source, compiled app/config contract versions, distinct mission-OFF and mission-ON tag/digest pairs, selected ON digest, OFF rollback digest, and prior digest;
- Home source, deterministic `dist.tar.gz` SHA-256, Cloudflare preview candidate ID, and current prior production ID;
- analytics privacy mode/region/retention/access/deletion inputs;
- actual AI primary/fallback model configuration, prompt hash, and fixture revision/hash;
- distinct staging/production identity, plus production-like journey origins and explicit Landing/app DNS overrides;
- exact producer-first order, 300-second sync detection, 900-second canary, Landing-last, 600-second reverse rollback budget, and retained backend/schema policy.

The browser harness receives the candidate file by absolute path and its SHA-256 out of band. It must parse those exact bytes. Journey evidence is `<artifact>/<journey>/evidence.json`, a non-empty JSON array whose rows contain exactly `route`, `step`, `result`, `duration_ms`, and `candidate_spec_sha256`.

## Final manifest and artifacts

All referenced GitHub artifacts must be unexpired, produced by the recorded successful workflow run, and scoped to the release ID. The release gate downloads them with `RELEASE_EVIDENCE_TOKEN` and verifies the exact `evidence.json` hash. Journey evidence uses the minimal row contract above. Other evidence is a sanitized JSON object with `status: "passed"` and the exact candidate SHA. Because a document cannot contain its own hash, the successful validator run also emits `<release_id>-sealed-validation`; that out-of-band envelope binds the final manifest's raw SHA-256, candidate SHA, validator run/attempt, and completed staging reverse duration. Promotion and rollback recompute the final raw hash and require that exact successful-run envelope.

The Home artifact is the only artifact allowed a second file: deterministic `dist.tar.gz`, whose hash must equal `home.dist_sha256`. It is extracted with traversal/link/device rejection and deployed without rebuilding. Release evidence must never contain user-authored code, runtime output, error text, prompts, context snapshots, diagnostic answers, email, OAuth material, guest IDs, or credentials.

## Protected workflows

Configure required reviewers (and prevent self-review) for every environment below. Each job also queries the GitHub environment API and fails if a `required_reviewers` protection rule is absent.

| Environment | Purpose | Required secrets |
|---|---|---|
| `mission-spine-staging` | exact OFF→ON staging validation and reverse rehearsal | `STAGING_KUBECONFIG_B64`, `RELEASE_EVIDENCE_TOKEN` |
| `mission-spine-production-off` | verify successful validation/artifacts, then approved OFF digest CAS promotion | `RELEASE_EVIDENCE_TOKEN`, `PRODUCTION_KUBECONFIG_B64` |
| `mission-spine-production-on` | separate manual ON approval, ≤5m sync, 15m canary | `PRODUCTION_KUBECONFIG_B64` |
| `mission-spine-production-landing` | verify completed ON canary, then deploy Home dist last | `RELEASE_EVIDENCE_TOKEN`, `CLOUDFLARE_API_TOKEN` |
| `mission-spine-production-rollback` | Landing prior first, web OFF, then web prior | `RELEASE_EVIDENCE_TOKEN`, `PRODUCTION_KUBECONFIG_B64`, `CLOUDFLARE_API_TOKEN` |

`RELEASE_EVIDENCE_TOKEN` needs read access to each recorded private Actions artifact. `CLOUDFLARE_API_TOKEN` needs Pages Write. Missing secrets, artifacts, model results, environment protection, cluster state, or deployment identity fail closed.

### Order

1. `mission-spine-validate.yml` validates the candidate-spec and GitOps base CAS, checks out the exact sealed Home SHA, runs `npm ci`, installs pinned Chromium support, and runs exactly Home's two release journeys against the candidate bytes/hash. It validates and byte-copies the two five-field arrays, uploads them separately, discovers the remaining successful source-pinned artifacts, and seals a final manifest with the current GitOps run/attempt attestation. Only that new final manifest is committed to the release branch. The same protected run then validates the final bundle, stages exact OFF/ON digests, rehearses ON→OFF→prior within 600 seconds, and leaves staging on the recorded prior digest. If the run fails after cluster setup, a best-effort fail-safe restore targets that same recorded prior digest.
2. `mission-spine-promote.yml` makes a normal fast-forward commit changing only the web Kustomization to OFF. After its environment approval and ≤5-minute sync, a second protected job selects the manifest's exact ON digest, waits for sync, and holds a full 900-second canary. It never rebuilds an image.
3. `mission-spine-landing-last.yml` requires the completed trusted promotion/canary artifact and the exact `base → OFF → ON` commit chain. It verifies the Cloudflare preview candidate and current prior-production CAS, then uploads the sealed Home dist to the production branch without rebuilding.
4. `mission-spine-rollback.yml` first invokes the Cloudflare Pages rollback endpoint for the recorded prior deployment, then changes only web to mission-OFF and finally the recorded prior digest. Additive service APIs and V1011 remain in place.

Normal non-force pushes provide the final branch CAS: any `main` drift makes the release commit non-fast-forward and fails. The scripts additionally compare `origin/main` to `gitops.base_sha` before the first mutation and verify the exact promotion chain before Landing or rollback. Staging uses one global concurrency lock, while promotion and Landing-last share one production lock across release IDs. Emergency rollback has its own serialized lock so it cannot be trapped behind the 15-minute canary; its exact Git/Cloudflare/deployment CAS fails closed if the promoted ON chain is not current, and the canary continuously rechecks the exact ON digest so a rollback invalidates the promotion run.

## Local validation

```bash
python -m pip install jsonschema==4.25.1
python -m unittest discover -s tests/release -p 'test_*.py' -v
python -m json.tool release-manifests/schema-v1.json >/dev/null
actionlint -color
kubectl kustomize apps/devpath-web/base >/dev/null
```

The Kustomize mutator removes `newTag` and writes the supported immutable form:

```yaml
images:
- name: ghcr.io/devpathai/devpath-web
  newName: ghcr.io/devpathai/devpath-web
  digest: sha256:<64 lowercase hex>
```

## Pending integration seam

This lane deliberately does not update existing Shared/migration or AI service pins. A real candidate-spec must carry the final merged Shared and AI source/image digests, and its `gitops.base_sha` must name the later GitOps commit that contains those pins. Until those artifacts and the V1011 operational approval exist, no candidate can pass resolution.

Cloudflare rollback uses the documented `POST /accounts/{account_id}/pages/projects/{project_name}/deployments/{deployment_id}/rollback` API. Only a successful production deployment recorded as `prior_production_deployment_id` is accepted.
