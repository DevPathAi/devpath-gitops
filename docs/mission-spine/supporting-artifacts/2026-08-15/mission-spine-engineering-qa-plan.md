# Mission Spine Engineering QA Plan

Generated: 2026-08-15 17:52 KST  
Scope: Landing, Web, Mobile, Admin, Learning, Sandbox, Review, LCS, Mentor, analytics, release controls  
Source plan: `deepe-unknown-design-20260815-144643.md`

## Release decision

Mission Spine can be promoted only when the owner-level contract suites are green, both cross-service browser journeys pass against the same immutable release manifest, required visual/accessibility evidence exists, the manifest names distinct immutable mission-OFF and mission-ON digests, a manually approved promotion selects one exact digest, and the prior-digest rollback rehearsal completes within budget. A passing widget test alone is not release evidence.

## Affected routes and surfaces

| Surface | Routes / entry points | Primary verification |
|---|---|---|
| Landing | `/`, header/hero/pricing/end CTA | Outcome proof, canonical `/diagnostic` handoff, journey continuity, no-JS fallback |
| Web diagnostic | `/diagnostic`, `/auth/callback`, `/consent` | Track → 15 questions → retained preview → OAuth → consent → idempotent claim |
| Web activation | `/dashboard` or canonical Today route, `/path`, `/content/:id` | Authoritative first incomplete task, current-week hierarchy, direct mission open |
| Web workspace | canonical task/content Sandbox route, Review surface, `/mentor` | Route-keyed context, starter language, durable run/review recovery, approved LCS context |
| Mobile companion | onboarding handoff, Home/Today, Learn, quick capture | Same server path, offline/stale visibility, task deep link, no duplicate diagnosis |
| Admin | dashboard, users | Responsive KPI, localized status, shared brand/system conformance |
| Install/share | Web/Admin PWA, Android, iOS, OG/favicons | Leva asset inventory and old Flutter/DevPath asset absence |

## Critical paths

### Journey A — activation

1. Open production-built Landing with analytics permission absent.
2. Confirm no analytics network request; activate the primary diagnostic CTA.
3. Confirm opaque journey handoff reaches `/diagnostic` and is removed from the visible URL immediately.
4. Select a track, complete all 15 questions, and view a result preview without signing in.
5. Refresh at track, question, preview, OAuth return, consent, claim, and saved-preview phases.
6. Complete deterministic OAuth and required consent; execute the claim twice to simulate a callback replay.
7. Confirm the saved result is deeply equal to the pre-login preview and owned by the same user.
8. Activate the explicit next action and confirm authoritative Today opens the first incomplete stable `taskId`.
9. Complete a content-linked task by crossing its content-progress completion threshold; confirm Today refetches and advances to the next stable `taskId`.
10. Complete a contentless task through its explicit completion action; replay both completion writes and confirm the same outcome with no duplicate advancement.
11. Verify ordered, deduplicated analytics through `first_mission_started` without raw answers, code, errors, prompts, email, GitHub handle, or guest token.

### Journey B — contextual practice

1. Open Today and enter Content through the canonical task/content route.
2. Enter Sandbox; verify task, content, language, starter code, editor draft, and Context Capsule correspond.
3. Start an accepted real-runtime run and receive a stable session ID within 1 second while owner recovery exposes `ALLOCATING`/`RUNNING`; disconnect immediately and midstream without changing execution persistence.
4. Recover through the authenticated owner GET and verify the eventual terminal result, `TIMED_OUT` on timeout, persisted `truncated` after reload, and stale-`ALLOCATING`/`RUNNING` reconciliation.
5. Observe deterministic Kafka/outbox-driven Review for the recovered terminal run.
6. Preview LCS fields, explicitly select context, commit a private `mentor_prompt` snapshot, and ask Mentor.
7. Capture the mock provider payload and prove it exactly matches the owner- and purpose-approved fields.
8. Confirm Review/Mentor partial failures retain editor, logs, terminal run evidence, and the last valid review.
9. Verify ordered analytics through `contextual_review_viewed` and assert banned values are absent from URL, SDK payload, request logs, and artifacts.

## Owner-level contract and integration matrix

| Owner | Required evidence | Failure branches |
|---|---|---|
| `devpath-home-page` | Vitest event/URL contract; Playwright against `dist`; four breakpoint smoke | blocked analytics SDK, no JS, navigation before SDK load, stale app target |
| `devpath-frontend` / `dp_core` | old/mixed/new JSON fixtures; typed `LearningPathApi`; both completion-path invalidation/refetch; reducer/provider tests | missing additive fields, corrupt continuation, route switch, late response, below-threshold progress, completion replay, no path, completed path |
| `devpath-learning-svc` | PostgreSQL source-of-truth claim IT; legacy Redis-marker migration/denial; current-mission and both-completion MVC/statement-count/size tests | concurrent same user, unbound/cross-owner marker, expired/missing marker DB fallback, rollback, Redis cleanup failure, below-threshold/replay/non-owner completion, malformed path |
| `devpath-shared` | claim uniqueness plus additive Sandbox status/truncation migration/constraint tests | duplicate `source_guest_id`, existing Sandbox rows/old-reader compatibility, non-destructive rollback policy |
| `devpath-sandbox-svc` | accepted-run early-ID timing; `ALLOCATING → RUNNING → terminal` persistence; bounded executor, owner recovery, best-effort SSE tests | queue full, immediate/midstream disconnect, `TIMED_OUT`, persisted truncation, output storm, pod drain, stale `ALLOCATING`/`RUNNING` reconciliation |
| Review pipeline | outbox/Kafka correlation and deterministic review | duplicate delivery, missing result event, result event ignored by old consumer, persisted terminal run after disconnect |
| `devpath-lcs-svc` | real Redis + PostgreSQL purpose/TTL/owner tests | expired draft, purpose escalation, selected-field mismatch, private visibility violation, cleanup/retry |
| `devpath-ai-svc` | LCS client contract, provider-payload capture, SSE/persistence IT | LCS unavailable/partial, owner/purpose denial, delimiter injection, provider failure before/after stream |
| GitOps/release | publish-only proof; distinct immutable OFF/ON digests; exact-digest manual promotion; mixed-version smoke; 15-minute canary; Landing-last and prior-digest rollback rehearsal | same-SHA tag/digest collision, publish job mutates GitOps, wrong digest promoted, canary regression, Landing deployed early, prior digest unavailable |

## Edge-case checklist

- Exact 30-minute guest-continuation boundary; expired and corrupt payloads; unknown codec version.
- OAuth callback twice, callback in another tab, consent denied, consent saved after refresh, claim response lost.
- Pre-seeded legacy/unbound, expired, or cross-owner Redis claim markers never authorize the caller; missing/expired cache falls through to the database source of truth, and any transitional owner-bound marker expires within 30 minutes.
- User already owns an active path; generated-path and existing-path cohorts remain distinct.
- Empty milestones, all tasks complete, optional and required task ordering, missing content, non-owner completion.
- Content-linked progress below threshold does not complete; the first threshold-crossing write advances Today; replay is a no-op with the same outcome.
- Contentless explicit completion advances Today; replay does not advance twice; non-owner writes are denied on both completion paths.
- Dashboard and Path request Today concurrently; one request is shared and either accepted completion path invalidates it immediately.
- Offline mobile snapshot older than 24 hours is not presented as current; offline completion is not shown as server-confirmed.
- Task A → B → A preserves separate drafts; late A response cannot mutate B; inactive workspace count stays bounded.
- JAVA/NODE/PYTHON starter matrix; aliases and unsupported language; deep-link reload and back/forward.
- Sandbox output exceeds 256 KiB or 2,000 rendered lines; event exceeds 16 KiB; accepted run disconnects before the session event or midstream; timeout reload reports `TIMED_OUT`; owner recovery retains `truncated`.
- LCS fields are deselected, unknown, stale, conflicting, or injected with closing delimiters.
- No context snapshot sends zero supplemental fields; recent errors/output default OFF; code opt-in applies to one request only.
- PostHog is unavailable, blocked, slow, or opted out; product actions remain immediate and successful.
- Mixed-version payload contains absent, present, or unknown additive fields; flag OFF retains the current journey.
- Dark/light, 200% text, reduced motion, keyboard-only, screen-reader focus return, and narrow landscape.

## Accessibility and visual evidence

Automated gates:

- Permanent goldens for Diagnostic Preview, Today, Path current week, Content, Workspace, Context Capsule, Review, Mentor, mobile Today, and Admin KPI at Compact/Medium/Expanded/Large and light/dark where applicable.
- Widget/DOM semantics: one primary action, meaningful heading order, selected navigation truth, live-region restraint, 44 px targets, focus visibility, focus return, and no focus trap.
- 200% text and localized long-copy overflow checks; reduced-motion behavior; color-token contrast tests.
- Landing browser accessibility scan against production `dist`; Flutter semantics tests for shared primitives and representative screens.

Manual pre-release evidence:

- NVDA + Chromium: Journey A and workspace recovery.
- VoiceOver + Safari/iOS: mobile Today, reading, offline/stale indication, task deep link.
- TalkBack + Android: mobile navigation, progress, quick capture, error recovery.
- Install/share previews for PWA, Android, iOS, favicon, and OG assets.

## LLM evaluation gates

The browser suites use deterministic model doubles. Real-model evaluation is tagged and release-associated.

PR gate, 100% required:

- owner, purpose, visibility, field allowlist, snapshot/provider/persistence parity;
- no-snapshot means no supplemental context;
- excluded field and cross-user data never reaches the LLM boundary;
- delimiter escaping and injection handling in every untrusted field;
- no raw snapshot, code, error, or prompt in logs, analytics, URLs, or failure artifacts.

Tagged model gate:

- Run for every primary and fallback model named in the release manifest when prompt/context/policy/model changes and before release.
- Cover paired no-context/approved-context, mission grounding, one-request code opt-in, error/output OFF and ON, missing/partial/stale/conflicting/irrelevant context, Korean clarity, and actionable next steps.
- Hard privacy/security invariants: 100%.
- Usefulness/grounding: at least 90% and no more than five percentage points below the pinned baseline.
- Record only synthetic case ID, model/config, prompt hash, fixture revision, category score, and latency.

## Performance and resilience gates

| Target | Budget / assertion |
|---|---|
| Current mission API | p95 ≤300 ms, p99 ≤800 ms, ≤3 DB statements, ≤20 KB |
| Mission visible | cold-4G p75 ≤1.5 s; optional dashboard metrics cannot block |
| Completion freshness | either accepted completion path refetches and advances Today within 1 s; replay does not double-advance |
| Sandbox output | combined stdout/stderr ≤256 KiB; SSE event ≤16 KiB; rendered lines ≤2,000 |
| Sandbox terminal durability | accepted-run session ID within 1 s; immediate/midstream disconnect cannot alter terminal persistence; owner GET recovers status/truncation; zero accepted sessions remain `ALLOCATING` or `RUNNING` for more than 35 s |
| Mentor | TTFT p95 ≤5 s; explicit terminal outcome ≥99% |
| Analytics | 0 bytes before permission; navigation delay 0 ms; loader target ≤20 KiB gzip |
| Flutter web bundle | `main.dart.js` gzip growth ≤50 KiB and ≤3% |
| Operations | rollout detection ≤5 min; recorded prior-digest rollback ≤10 min |

## Release matrix

1. Old frontend + additive new APIs.
2. Build and publish mission-OFF and mission-ON artifacts as distinct immutable tags/digests; prove publish performs no deployment or GitOps mutation.
3. New frontend OFF + old-compatible API fixtures and additive new APIs.
4. New frontend ON + new APIs.
5. Manually approve and promote the exact selected digest; run mixed-version smoke and a 15-minute canary.
6. Mobile current release + additive API, then new mobile release + new API.
7. Landing remains on the old CTA until app/backend ON-digest validation and canary are complete.
8. Reverse rehearsal: Landing previous deployment → recorded frontend prior digest → additive backend retained.

## Staging prerequisites

- Deterministic OAuth test provider and required-consent state control.
- Isolated PostgreSQL, Redis, Kafka/outbox, Sandbox runtime images, LCS, Review, and AI provider double.
- PostHog-compatible analytics spy with allowlist and banned-property inspection.
- Release manifest containing every service revision, both OFF/ON digests, selected promoted digest, prior digest, frontend flag/contract version, actual model primary/fallback, and migration version.
- Seed fixtures for new path, existing active path, expired guest, legacy/unbound/cross-owner/expired claim markers, all-complete path, content-linked task below/at threshold, contentless task, task ownership mismatch, Sandbox `ALLOCATING`, `TIMED_OUT`, `truncated`, stale `ALLOCATING`, stale `RUNNING`, and LCS expiry.
- Production-built Landing and immutable Web/Admin artifacts; mobile signed test artifacts before distribution verification.

## Evidence bundle

The release record must link owner-suite results, the two browser traces, goldens, accessibility evidence, performance measurements, model-eval summary, artifact-size diff, publish-without-deploy proof, distinct OFF/ON digests, manual approval record, exact promoted/prior digests, release manifest, 15-minute canary result, and reverse-rollback timing. It must not contain user-authored code, errors, prompts, snapshots, or raw diagnostic answers.
