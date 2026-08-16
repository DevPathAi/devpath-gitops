# Mission Spine release control

Mission Spine releases use two immutable JSON documents. This avoids a self-referential hash: browser journeys cannot produce evidence hashes until they have consumed the release inputs.

## Documents

1. `candidates/<release_id>.candidate-spec.json` is the canonical execution input. It contains only immutable source/image/config/environment inputs, `journey_harness`, and the quality case-catalog/build bindings; it contains no journey or quality result.
2. `releases/<release_id>.json` is the final release manifest. It binds the exact candidate-spec SHA-256 and the Home dist, privacy approval, AI release evaluation, two browser journeys, and seven distinct quality-evidence manifests: frontend visual, Home visual, frontend automated accessibility, Home axe/browser accessibility, manual NVDA, manual VoiceOver, and manual TalkBack. Every evidence reference repeats `candidate_spec_sha256`.

Both documents conform to `schema-v1.json`. `release_id` must match `^ms-[0-9]{8}-[a-z0-9][a-z0-9-]{2,40}$`. Promotion workflows accept only that ID. A digest, manifest path, branch, cluster, or Cloudflare deployment cannot be supplied as a workflow input. A candidate branch must descend from the exact `gitops.base_sha` and differ by only its canonical candidate file; after validation it must differ by exactly that candidate plus its canonical final manifest. Every production lane rechecks this branch shape before consuming secrets or mutating state.

No production candidate is checked in by this change because the real immutable images, successful workflow artifact IDs, model evaluation, browser journeys, quality results, and approvals do not yet exist. The synthetic files under `tests/release/fixtures/` are contract tests only and cannot be resolved by a release workflow.

## Candidate-spec contract

The candidate-spec binds:

- GitOps repository and exact `base_sha`, the one trusted legacy web tag plus its prior digest, and the one mutable path `apps/devpath-web/base/kustomization.yaml`;
- source SHA plus immutable OCI digest for every application service;
- Shared source, immutable version/JAR SHA-256, migration image, Flyway target `202608161011`, required V1011 validation, and the additive-retained rollback policy;
- frontend source, compiled app/config contract versions, distinct mission-OFF and mission-ON tag/digest pairs, selected ON digest, OFF rollback digest, and prior digest;
- Home source, deterministic `dist.tar.gz` SHA-256, Cloudflare preview candidate ID, and current prior production ID;
- analytics privacy mode/region/retention/access/deletion inputs;
- actual AI primary/fallback model configuration, prompt hash, and fixture revision/hash;
- distinct staging/production identity, plus production-like journey origins and explicit Landing/app DNS overrides;
- exact repository/source SHA, catalog path/version/SHA-256, case count, and render/test input-provenance SHA-256 for all seven quality lanes; frontend v1 additionally fixes the ordered 12-fixture ID list, the generated visual catalog at 96 cases (`Web` 48, `Admin` 16, `Mobile` 16, `dp_design` 16), and the generated automated-accessibility catalog at 24 cases (`Web` 12, `Admin` 4, `Mobile` 4, `dp_design` 4); both frontend catalogs declare `capture_surface=flutter_web_release_projection` and `device_evidence=false`, so their `Mobile` cases cannot satisfy signed native-build or manual TalkBack evidence;
- Home's evidence-producer SHA separately from its rendered-product SHA, plus the rendered product-tree and font-manifest SHA-256 values; the Home runtime provenance is the SHA-256 of its sorted-key, compact JSON runtime object and the two Home manifests must bind the same catalog and render inputs;
- frontend mobile-test source SHA and signed build-provenance, APK, and IPA SHA-256 values; VoiceOver must bind the IPA/build pair and TalkBack must bind the APK/build pair;
- exact producer-first order, 300-second sync detection, 900-second canary, Landing-last, 600-second reverse rollback budget, and retained backend/schema policy.

The browser harness receives the candidate file by absolute path and its SHA-256 out of band. It must parse those exact bytes. Journey evidence is `<artifact>/<journey>/evidence.json`, a non-empty JSON array whose rows contain exactly `route`, `step`, `result`, `duration_ms`, and `candidate_spec_sha256`. Both row order and the route/step pairs are allowlisted for the two approved journeys; free-form detail fields are rejected.

## Final manifest and artifacts

All referenced GitHub artifacts must be unexpired and come from an exact successful run at the bound source SHA. Each reference binds repository, producer head SHA, run ID and attempt, canonical workflow path, the full workflow-blob SHA-256, artifact ID/name, selected evidence filename/hash, and candidate-spec hash; suffix/path lookalikes and branch-name-only provenance are rejected. Frontend visual and automated-accessibility artifacts are release-scoped `workflow_dispatch` artifacts. Manual NVDA, VoiceOver, and TalkBack use their exact static artifact names from the frontend-owned manual-AT workflow. Home visual and Home axe/browser accessibility are generated after candidate validation by the digest-pinned Home wrapper and uploaded as two sanitized manifests in one `<release_id>-home-visual-a11y-attempt-<run_attempt>` artifact owned by the exact GitOps `.github/workflows/mission-spine-validate.yml` run. Their run, attempt, head, workflow hash, and physical artifact ID must match the two journeys and validation attestation, while their selected filenames and hashes remain distinct. A normal Home `.github/workflows/ci.yml` push artifact is preflight-only and is rejected as release evidence. Since GitHub artifact metadata exposes the run ID but not the producing rerun attempt, each non-journey `evidence.json` result additionally carries exact positive-integer producer run/attempt data that must match the live run record; Home v2 manifests bind that identity through the shared validation-run artifact reference and live run lookup.

The validator proves the allowed `base → candidate-only → final-only` Git tree. The release gate downloads artifacts with `RELEASE_EVIDENCE_TOKEN` and verifies each exact selected-file hash and the exact allowed file set. Journey evidence uses the minimal row contract above. Quality evidence accepts only enumerated sanitized aggregates or Home's bounded per-case IDs, counts, viewports, and hashes: the exact catalog version and raw digest, the ordered frontend fixture IDs, the exact catalog and surface counts, all cases passed, zero failures, exact precomputable render/test input provenance, and zero critical/serious automated-accessibility violations. Frontend visual evidence additionally requires zero pixel difference. Post-run result-manifest and artifact-set hashes stay in the frontend-owned detailed candidate artifact and are not interchangeable with the candidate's pre-run input-provenance binding. Home v2 evidence binds its deterministic browser/container/font/theme/baseline provenance, requires zero accessibility violations at every severity, and must have an approved baseline before release. Raw screenshots, accessibility findings, notes, free-form content, and diagnostic output are not accepted. GitOps-owned journey and sealed-validation artifact names include `-attempt-<github.run_attempt>`, and sealing fails unless the read-only journey job ran in the current attempt. Because a document cannot contain its own hash, the successful validator run emits `<release_id>-sealed-validation-attempt-<run_attempt>`; that out-of-band envelope binds the final manifest's raw SHA-256, candidate SHA, validator head/workflow/run/attempt, and completed staging reverse duration. Promotion and rollback recompute the final raw hash and require that exact successful-run envelope.

The Home dist artifact contains exactly `evidence.json` and deterministic `dist.tar.gz`, whose hash must equal `home.dist_sha256`; it is extracted with traversal/link/device rejection and deployed without rebuilding. Separately, the combined Home quality artifact contains exactly `visual-evidence.v2.json` and `a11y-evidence.v2.json`. Every other evidence artifact contains exactly its selected `evidence.json`. Release evidence must never contain user-authored code, runtime output, error text, prompts, context snapshots, diagnostic answers, email, OAuth material, guest IDs, or credentials.

## Protected workflows

Configure required reviewers (and prevent self-review) for every environment below. Each job also queries the GitHub environment API and fails if a `required_reviewers` protection rule is absent.

| Environment | Purpose | Required secrets |
|---|---|---|
| `mission-spine-staging` | read-only Home journeys, trusted seal, exact OFF→ON validation, reverse rehearsal | `MISSION_RELEASE_CONTROL_TOKEN`, `MISSION_SYNTHETIC_PROBE_TOKEN`, `STAGING_KUBECONFIG_B64`, `RELEASE_EVIDENCE_TOKEN` |
| `mission-spine-production-off` | verify successful validation/artifacts and live prior CAS, then approved OFF digest promotion | `RELEASE_EVIDENCE_TOKEN`, `PRODUCTION_KUBECONFIG_B64`, `MISSION_SYNTHETIC_PROBE_TOKEN` |
| `mission-spine-production-on` | separate manual ON approval, ≤5m sync, 15m canary | `PRODUCTION_KUBECONFIG_B64`, `MISSION_SYNTHETIC_PROBE_TOKEN` |
| `mission-spine-production-landing` | verify completed ON canary, then deploy Home dist last | `RELEASE_EVIDENCE_TOKEN`, `CLOUDFLARE_API_TOKEN` |
| `mission-spine-production-rollback` | Landing prior first, web OFF, then web prior | `RELEASE_EVIDENCE_TOKEN`, `PRODUCTION_KUBECONFIG_B64`, `CLOUDFLARE_API_TOKEN` |

`RELEASE_EVIDENCE_TOKEN` needs read access to each recorded private Actions artifact and read-only checkout access to Home. `MISSION_SYNTHETIC_PROBE_TOKEN` authenticates the release-specific readiness endpoint. `CLOUDFLARE_API_TOKEN` needs Pages Write. Missing secrets, artifacts, model results, environment protection, cluster state, or deployment identity fail closed.

### Order

1. `mission-spine-validate.yml` validates the candidate-spec and GitOps base CAS in a read-only job, checks out the exact sealed Home SHA without persisted credentials, verifies the canonical candidate's raw bytes/out-of-band hash, runs Home's digest-pinned local production-dist visual/a11y wrapper with those inputs, uploads the exact two sanitized manifests, and then runs exactly Home's two live release journeys against the same candidate bytes/hash. The external Home code never runs with `contents: write`, and the visual/a11y step receives no live credential. A separate trusted job downloads all three current-run artifacts by exact ID, discovers the remaining successful source-pinned artifacts, validates and seals only the final manifest, then validates live staging prior identity, OFF/ON, and ON→OFF→prior within 600 seconds.
2. `mission-spine-promote.yml` first proves both the sealed legacy tag binding and actual ready Pod `imageID` equal the prior digest. It then makes a normal fast-forward commit changing only the web Kustomization to OFF. Every sync poll verifies desired/updated/ready/available replica counts, nonterminating ready Pods, exact runtime digest, stable Pod/restart baseline, and release-specific synthetic identity. A separately approved job selects the exact ON digest and holds a full 900-second canary under the same checks.
3. `mission-spine-landing-last.yml` requires the completed trusted promotion/canary artifact and exact `base → OFF → ON` chain. A secret-free job installs Wrangler 4.123.0 from the committed npm integrity lock and transfers that exact installation to the protected job; there is no runtime `npx` install. The protected job verifies Cloudflare prior CAS, adds a content-addressed public dist marker, deploys without rebuilding, captures the newly created deployment ID, and verifies both exact-current CAS and marker bytes.
4. `mission-spine-rollback.yml` shares the production lease and sets `cancel-in-progress: true`, so it preempts promotion or Landing. It safely resumes from this release's exact prior, OFF, or ON Git phase; rolls Landing prior first; changes web through OFF when necessary and then prior; and finally rechecks that Landing prior is still current. Additive service APIs and V1011 remain in place.

Normal non-force pushes provide the final branch CAS: any `main` drift makes the release commit non-fast-forward and fails. The scripts additionally compare `origin/main` to `gitops.base_sha` before the first mutation and verify the exact promotion chain before Landing or rollback. Staging has one global concurrency lock. Promotion, Landing-last, and rollback share one production lock across release IDs; rollback preempts it and then re-establishes exact Git, Kubernetes, and Cloudflare CAS before each mutation.

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

The contract fixture now binds the composed source commits for Shared, AI, Learning, Gateway, Platform, Community, Notification, LCS, Sandbox, frontend, and Home, plus the approved frontend catalog shape and synthetic catalog digests, provenance digests, signed mobile-build hashes, and evidence IDs. It deliberately remains a synthetic `ms-2099` document: its image digests, catalog/build hashes, and evidence IDs are not release artifacts. The frontend source SHA and the two synthetic generated-catalog digests must be rebound after the producer catalogs land; the fixture does not substitute for those producer artifacts. A real candidate-spec may be created only after immutable images, the frontend automated-accessibility producer, all seven source-pinned quality results, Home/approval/evaluation artifacts, operational V1011 approval, and the later GitOps `base_sha` containing the corresponding configuration pins exist. Home's ordinary push evidence defaults to its repository-local visual candidate hash and its refreshed baseline is still pending external review. Only the GitOps validator's candidate-bound wrapper output with an approved baseline can be sealed; the verifier intentionally rejects the current preflight artifact rather than substituting either value.

Cloudflare rollback uses the documented `POST /accounts/{account_id}/pages/projects/{project_name}/deployments/{deployment_id}/rollback` API. Only a successful production deployment recorded as `prior_production_deployment_id` is accepted.
