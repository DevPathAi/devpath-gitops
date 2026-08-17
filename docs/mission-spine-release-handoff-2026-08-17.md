# Mission Spine 릴리스 핸드오프 — 2026-08-17

## 1. 목적과 현재 판정

이 문서는 Mission Spine 릴리스 코드와 producer 변경을 다음 운영자에게 안전하게 넘기기 위한 인수 문서다. 기준일은 2026-08-17(KST)이다.

설계·QA·검토 원본과 외부 문서 이동 기록은 [Mission Spine 문서 인덱스](mission-spine/README.md)에서 찾는다.

Mission Spine 밖에서 이월된 운영 미해결 건은 [11. 이월 작업 — 학습경로 생성](#11-이월-작업--학습경로-생성2026-08-14-핸드오프-이월)에 있다. 그중 학습경로 생성은 Mission Spine의 「현재 미션」 화면이 읽는 데이터를 만드는 경로이므로 릴리스와 무관한 별건이 아니다.

현재 판정은 **코드 통합 진행 가능, 패키지 게시·candidate 생성·production dispatch·배포는 HOLD**다. 이 작업에서 관리자 우회, 강제 푸시, 환경 승인, 패키지·이미지 게시, Kubernetes 변경, Cloudflare 배포는 수행하지 않았다.

HOLD는 다음 조건 중 하나라도 남아 있으면 해제할 수 없다.

- 독립 reviewer가 없거나 self-review 방지가 실제 승인자를 막는 경우
- protected environment의 `can_admins_bypass`가 `false`가 아닌 경우
- evidence-reader/release App, 환경 비밀, branch protection/ruleset이 코드 계약과 다른 경우
- Shared immutable Maven 좌표가 게시·재다운로드 검증되지 않은 경우
- producer main SHA의 immutable image 및 evidence가 없는 경우
- Cloudflare Pages production writer가 하나로 제한되지 않은 경우
- GitOps base SHA가 최종 producer SHA·digest와 재바인드되지 않은 경우

영구 릴리스 계약은 [release-manifests/README.md](../release-manifests/README.md)를 단일 기준으로 사용한다.

## 2. 저장소 및 PR 스냅샷

### 완료된 기반

| 저장소 | 기본 브랜치 SHA | 상태 |
|---|---|---|
| Home | `d0585602cf831f1895f0dc8997c667a23d99e9de` | PR #38 병합, `test`와 `visual-a11y` 성공 |
| Documents | `7f722d281a704a1b28e4784a75a06e1271a7b3c9` | PR #97 병합, `privacy-producer-contract` 성공 |
| Shared | `d2fa542609976ead2a2f4f3ef3fca4fbba459ff8` | PR #65 병합, main CI 성공 |

### 통합 중

| 범위 | PR/브랜치 | 현재 상태와 다음 조치 |
|---|---|---|
| Frontend evidence-reader 분리 | [Frontend PR #134](https://github.com/DevPathAi/devpath-frontend/pull/134), head `8d2c5279749295868b77dd1294fbbbd023d7ba09` | 전 체크 GREEN 후 merge commit `0c35edbcd9281d48fd3b0a80233223a246ed90b0`로 `develop`에 병합 완료 |
| Frontend 릴리스 | [Frontend PR #133](https://github.com/DevPathAi/devpath-frontend/pull/133), head `0c35edbcd9281d48fd3b0a80233223a246ed90b0` | CI `32004243899`, Mobile `32004243827`, ET13 `32004243822` GREEN. `BLOCKED`, 독립 승인 전 main 병합 금지 |
| Shared 릴리스 | [Shared PR #67](https://github.com/DevPathAi/devpath-shared/pull/67) | build 성공, 독립 reviewer 승인 필요 |
| GitOps release control | [GitOps PR #58](https://github.com/DevPathAi/devpath-gitops/pull/58), head `e3ed89b7a0d80566b6d197200a485d2a129ed360` | 전 체크 GREEN 후 merge commit `2680de8286a21ab52051ee2599b47c9fd108bd97`로 `develop`에 병합 완료 |
| GitOps 릴리스 | [GitOps PR #59](https://github.com/DevPathAi/devpath-gitops/pull/59), protected-control 스냅샷 `32ca88e014c5ade42eed520346fa37913c4eced9` | main 동기화와 CI `32004437957` GREEN 확인. PR head는 이후 문서-only merge를 포함한 현재 `develop`이므로 실행 전 live SHA·체크를 다시 읽는다. Draft/HOLD이며 최종 producer 재바인드·외부 통제 전 main 병합 금지 |

### Shared immutable package로 차단된 producer

아래 PR의 build 실패 원인은 공통으로 `ai.devpath:devpath-shared:0.0.1-et9.20260816` 미게시다. image job은 PR에서 의도적으로 skip된다.

| 저장소 | PR head | PR |
|---|---|---|
| AI | `34c278745b44ac00b8c336742d4c0ea3e6e9c8f2` | [#35](https://github.com/DevPathAi/devpath-ai-svc/pull/35) |
| Community | `fa44d3910eb520b740c15642a3c1acf14c19a977` | [#35](https://github.com/DevPathAi/devpath-community-svc/pull/35) |
| Gateway | `5128adbb32f2b389739c3e4ca1806ddbcb056393` | [#31](https://github.com/DevPathAi/devpath-gateway/pull/31) |
| LCS | `5a49f66a6fe456189558fb47f7d07cc4d6b94271` | [#10](https://github.com/DevPathAi/devpath-lcs-svc/pull/10) |
| Learning | `7e64884349ae1a93a556ca5dad55b24ddf07d74a` | [#51](https://github.com/DevPathAi/devpath-learning-svc/pull/51) |
| Notification | `959d8efb92196588f9f31c94798e57ed2d36e57c` | [#14](https://github.com/DevPathAi/devpath-notification-svc/pull/14) |
| Platform | `158f648c08ceb04c5c2390c26b49aac0d69b5049` | [#52](https://github.com/DevPathAi/devpath-platform-svc/pull/52) |
| Sandbox | `2b59a93c41a53a8fdaf7b50fe8928403bc84ff53` | [#24](https://github.com/DevPathAi/devpath-sandbox-svc/pull/24) |

## 3. 이번 변경의 핵심

### Frontend

- ET13의 cross-repository 인증을 `mission-spine-et13-release-auth` 환경 job으로 분리했다.
- manual AT 입력 인증을 `mission-spine-manual-at-auth` 환경 job으로 제한했다.
- legacy org-wide `secrets.GITOPS_APP_ID`와 `secrets.GITOPS_APP_PRIVATE_KEY` 실행식을 제거했다.
- exact reviewer authority, non-self approval, main/attempt-1/current-run 검증을 추가했다.
- release-auth ZIP·metadata·raw/action bytes·canonical JSON을 bounded verifier로 검증한다.
- Python fixture가 `__pycache__`를 만들어 provenance clean-tree를 깨뜨리던 CI 회귀는 `python3 -B`와 계약 테스트로 닫았다.

비차단 부채: ET13 workflow에는 새 verifier 뒤에 기존 inline ZIP/schema 검증이 남아 있다. 두 경계 모두 fail-closed지만 후속 PR에서 verifier 출력 기반 단일 구현으로 통합한다.

### GitOps

- protected production workflow를 main-owned control checkout과 sealed data checkout으로 분리했다.
- `base → migration → additive services → mission OFF → mission ON → rollback` 체인을 재실행 가능하게 검증한다.
- 9개 서비스 digest를 원자적으로 적용하고 Argo/Kubernetes runtime의 exact image·owner·container shape를 재관찰한다.
- main PR rename/copy 우회, source-only fetch, stale run/attempt, self/admin-bypass approval을 fail-closed 처리한다.
- Landing evidence와 referenced canary를 rollback 첫 Cloudflare 변경 전에 다시 인증한다.
- Cloudflare API 응답을 no-redirect, JSON/UTF-8, identity encoding, declared length, 1 MiB 상한으로 제한한다.
- production kubeconfig는 임의 고유 경로·exclusive create·0600·exact cleanup으로 관리한다.
- Python cache 산출물은 `.gitignore`로 제외한다.

## 4. 필수 외부 설정

### 독립 reviewer

현재 조직에는 사실상 단일 사용자만 있어 PR review와 `prevent_self_review=true` 환경 승인을 정상 완료할 수 없다. 독립 reviewer 또는 팀을 먼저 추가하고, reviewer가 initiator·triggering actor와 다른지 확인한다. 관리자 우회는 대체 수단이 아니다.

### Frontend evidence-reader App

App slug는 `devpath-evidence-reader`로 고정한다.

- 설치 저장소: 필요한 GitOps/evidence 저장소만
- 권한: Actions read, Contents read, 필요한 경우 Members read
- write/admin 권한 금지
- 환경 비밀: `MISSION_SPINE_EVIDENCE_READER_APP_ID`, `MISSION_SPINE_EVIDENCE_READER_APP_PRIVATE_KEY`
- 배치 환경: `mission-spine-et13-release-auth`, `mission-spine-manual-at-auth`
- 각 환경: custom branch policy exact `main`, required reviewer, `prevent_self_review=true`, `can_admins_bypass=false`

Documents privacy와 AI eval도 legacy org secret 대신 별도 protected environment의 read-only evidence App으로 이동해야 한다.

### GitOps release App

App slug는 `devpath-gitops-release`로 고정한다.

- 설치 저장소: `DevPathAi/devpath-gitops` 단 하나
- 권한: Contents write, Administration read
- 환경 비밀: `GITOPS_RELEASE_APP_ID`, `GITOPS_RELEASE_APP_PRIVATE_KEY`
- 사용 환경: production OFF, ON, rollback과 Shared migration release 환경

GitOps main의 목표 server fence:

1. Integrity ruleset: deletion, non-fast-forward, required linear history; bypass 없음
2. Governance ruleset: update 제한; sole Integration actor `devpath-gitops-release`, bypass mode `always`
3. Classic protection: status checks `null`, admins enforced, linear history, review 1, stale review dismiss, last-push approval, conversation resolution, force/delete 금지, restrictions와 PR bypass allowance의 sole App 일치

설정 후 GET 응답을 코드 validator와 대조하고 다음 live probe를 수행한다.

- user/admin normal FF push 거부
- user PR merge 거부
- release App의 single-parent normal FF push 허용
- App force push와 branch deletion 거부

### 기존 org secret 폐기

조직 전체 공개 상태였던 `GITOPS_APP_ID`와 `GITOPS_APP_PRIVATE_KEY`는 노출된 자격증명으로 취급한다. 새 환경별 reader/release App이 준비된 뒤 기존 키를 회전·폐기하고 org secret 두 개를 삭제한다. producer diagnostic/PR job에는 App private key 표현식이 0개여야 한다.

### GHCR

target package namespace를 신뢰된 운영 identity로 먼저 생성하고 repository permission inheritance와 source repository의 package admin/write를 제거한다. branch-controlled `GITHUB_TOKEN`이 package push/delete를 할 수 없어야 한다. exact-main protected publisher만 classic PAT 기반 `write:packages`를 사용할 수 있다.

### Cloudflare

Cloudflare Pages API에는 expected-current 조건부 write가 없다. 따라서 repository concurrency만으로 dashboard/API/다른 repository writer와의 TOCTOU를 막을 수 없다.

- `production_deployments_enabled=false`
- production branch exact `develop`
- production write token은 GitOps protected landing/rollback 환경 하나에만 보관
- dashboard/manual/다른 CI의 production write 권한 폐기
- Landing/rollback 동안 별도 운영 lease로 sole writer 보장

이 조건을 증명할 수 없으면 Landing과 rollback을 실행하지 않는다.

## 5. 안전한 병합·게시 순서

1. 완료: Frontend PR #134를 exact-head CI GREEN 후 merge commit `0c35edbcd9281d48fd3b0a80233223a246ed90b0`로 `develop`에 병합했다.
2. Frontend PR #133은 새 develop head `0c35edbcd9281d48fd3b0a80233223a246ed90b0`에서 전 체크 GREEN이다. 독립 review 전에는 main에 병합하지 않는다.
3. 완료: GitOps PR #58을 merge commit `2680de8286a21ab52051ee2599b47c9fd108bd97`로 `develop`에 병합하고 main을 동기화한 protected-control 스냅샷 `32ca88e014c5ade42eed520346fa37913c4eced9`를 PR #59 Draft/HOLD로 열었다. PR head는 문서-only merge에 따라 이동하므로 live SHA를 다시 읽는다.
4. 독립 reviewer가 Shared PR #67을 main에 merge commit으로 병합한다.
5. resulting Shared main SHA의 fresh attempt-1 protected package publish를 승인한다. 취소된 run을 재사용하지 않는다.
6. 게시 후 Maven에서 `0.0.1-et9.20260816`의 JAR/POM/module bytes를 재다운로드해 frozen SHA-256과 비교한다.
7. AI와 7개 service PR 체크를 fresh rerun한다. build GREEN 후 feature→develop을 merge commit으로 병합한다.
8. 각 저장소의 develop→main release PR을 독립 review 후 merge commit으로 병합한다.
9. 각 final main SHA에서 immutable image와 run-scoped `evidence.json`을 attempt 1로 생성한다.
10. Frontend #133을 final develop SHA에서 main으로 병합하고 immutable web/admin image evidence를 생성한다.
11. 최종 producer SHA·image digest·workflow hash로 GitOps candidate fixture를 재바인드하고 GitOps main release PR을 마지막에 병합한다.
12. 외부 설정표를 모두 재검증한 뒤 candidate → protected producers → final manifest → validate → OFF → ON/canary → Landing-last 순서로 실행한다.

## 6. 단계별 STOP 조건

다음 중 하나라도 발생하면 후속 단계를 실행하지 않는다.

- PR head/base가 검토한 SHA와 다름
- rerun attempt가 계약과 다르거나 competing eligible run이 존재
- artifact metadata, raw ZIP, selected file hash, workflow blob hash가 다름
- default branch가 protected/current가 아니거나 환경 reviewer/admin-bypass 정책이 다름
- package/image tag가 absent-or-exact 계약을 벗어남
- OCI root→linux/amd64 child→config/rootfs/labels 관계가 다름
- GitOps main이 sealed base/phase commit과 다름
- Argo observed/applied revision 또는 Kubernetes runtime이 expected commit/digest와 다름
- Cloudflare current production, source marker, production branch, auto-deploy 설정이 다름
- sole-writer lease를 보장할 수 없음

## 7. 검증 증거

Frontend local 경계:

- full workspace analyze: 5/5 packages
- full workspace tests: 성공
- evidence/manual/baseline focused contracts: 성공
- protected approval Node tests: 12/12
- release-auth Python fixtures: 7/7
- ET13 producer contracts: 14/14
- independent staged audit: P0=0, P1=0, P2=1(중복 verifier, 비차단)

GitOps local 경계:

- release tests: 209 run, 0 failures, 0 errors, 3 Windows symlink skips
- actionlint 1.7.7: 성공
- release schema JSON parse: 성공
- Kustomize bases 13개: 성공
- `git diff --check`: 성공
- partial independent audit: code P0=0/P1=0; external Cloudflare sole-writer P1은 OPEN

GitHub CI URL은 각 PR의 Checks 탭을 권위값으로 사용하고, merge 직전에 PR head SHA와 job head SHA를 다시 비교한다.

## 8. 재시도·rollback 규칙

- protected producer의 attempt-1 계약을 rerun으로 우회하지 않는다. 실패 시 fresh dispatch와 새 run ID를 사용한다.
- promotion은 durable phase commit과 runtime 재관찰을 기준으로 재개한다. 이전 job output만 신뢰하지 않는다.
- same image의 다음 release도 release manifest SHA를 포함한 새 migration Job identity를 사용한다.
- Landing은 exact candidate deployment를 재사용할 수 있지만 foreign current deployment는 거부한다.
- rollback은 authenticated Landing evidence가 가리키는 deployment만 prior로 되돌린다.
- rollback 중 Cloudflare prior가 먼저 적용되고 Git phase가 남은 경우 fresh run이 prior 상태를 인증한 뒤 Git rollback phase를 계속한다.
- additive services와 migration schema는 rollback 후에도 유지한다.

## 9. 최종 인수 체크리스트

- [x] Frontend #134 exact-head CI GREEN 및 develop merge commit `0c35edbcd9281d48fd3b0a80233223a246ed90b0` 기록
- [ ] Frontend #133 final develop SHA `0c35edbcd9281d48fd3b0a80233223a246ed90b0`와 CI GREEN 기록 완료, 독립 reviewer 기록 필요
- [x] GitOps feature→develop merge `2680de8286a21ab52051ee2599b47c9fd108bd97`, protected-control 스냅샷 `32ca88e014c5ade42eed520346fa37913c4eced9`, develop→main PR #59 기록(실행 전 live head 재확인)
- [ ] Shared #67 independent review/main merge 기록
- [ ] immutable Shared package 게시와 3개 byte hash 기록
- [ ] AI + 7 services build/main SHA/image digest/evidence 기록
- [ ] Frontend final main SHA와 web/admin digest 기록
- [ ] 독립 reviewer/team 추가
- [ ] 모든 protected environment `can_admins_bypass=false`
- [ ] evidence-reader/release App 설치·권한·repo inventory 검증
- [ ] legacy org App key 회전·삭제
- [ ] GitOps ruleset/classic protection live probe 성공
- [ ] GHCR package ownership과 branch-token 403 probe 성공
- [ ] Cloudflare sole writer/lease 및 auto production deploy OFF 증명
- [ ] Kubernetes/Cloudflare/environment secrets를 최소 권한으로 배치
- [ ] final candidate GitOps base와 모든 producer SHA/digest 재바인드
- [ ] 실제 배포 전 별도 GO 기록
- [x] Mission Spine 외부 작성 산출물 16개를 workspace 내부로 해시 보존 이동하고 문서 인덱스·이동 대장을 기록

이 체크리스트가 모두 채워지기 전에는 “릴리스 완료” 또는 “production GO”로 표시하지 않는다.

## 10. 문서 정리 및 외부 산출물 이동

2026-08-17에 이번 작업의 설계·QA·검토·결정 이력을 `D:\workspace\dpa` 외부의 `.gstack` 프로젝트 저장소에서 이 GitOps 저장소의 `docs/mission-spine/supporting-artifacts/2026-08-15/`로 이동했다. 각 이동은 원본이 workspace 밖인지, 목적지가 workspace 안인지, 목적지가 비어 있는지 확인한 뒤 수행했으며 이동 전후 SHA-256이 같은지 검증했다. 아래 원본 경로에는 이동 완료 후 파일이 남아 있지 않다.

| 외부 원본 | 내부 파일 | bytes | SHA-256 |
|---|---|---:|---|
| `C:\Users\deepe\.gstack\projects\dpa\deepe-unknown-design-20260815-144643.md` | [`mission-spine-design.md`](mission-spine/supporting-artifacts/2026-08-15/mission-spine-design.md) | 137209 | `d901e288305a21129999f692badf16928a9170d572142edf1c3562ba7d7d5691` |
| `C:\Users\deepe\.gstack\projects\dpa\deepe-unknown-eng-review-test-plan-20260815-175227.md` | [`mission-spine-engineering-qa-plan.md`](mission-spine/supporting-artifacts/2026-08-15/mission-spine-engineering-qa-plan.md) | 14266 | `64e0580349ffd5754afed8d6edeb19b05e629c415ef07a895f1752db179baa2b` |
| `C:\Users\deepe\.gstack\projects\dpa\claude-independent-plan-review-20260815-183722.json` | [`independent-plan-review.json`](mission-spine/supporting-artifacts/2026-08-15/independent-plan-review.json) | 12489 | `fd0319854b6751f1d3485e58f65b5b04a2c60240f625b4b85d9018b208ae7321` |
| `C:\Users\deepe\.gstack\projects\dpa\tasks-design-review-20260815-162335.jsonl` | [`design-review-tasks.jsonl`](mission-spine/supporting-artifacts/2026-08-15/design-review-tasks.jsonl) | 6813 | `860ddc1733154a81c6899eb6c1fffb59cc71ef3f188b0522c8a1e6085d6a5572` |
| `C:\Users\deepe\.gstack\projects\dpa\tasks-eng-review-20260815-175227.jsonl` | [`engineering-review-tasks.jsonl`](mission-spine/supporting-artifacts/2026-08-15/engineering-review-tasks.jsonl) | 8911 | `e5fc9f22b95a3a2a2e17d1eb95d96bc8066ea38f57464bfc516654b0300bf656` |
| `C:\Users\deepe\.gstack\projects\dpa\question-log.jsonl` | [`decision-question-log.jsonl`](mission-spine/supporting-artifacts/2026-08-15/decision-question-log.jsonl) | 10855 | `e10b9ab689736e3b4a99a887c6fe9d0592ef70b6cd28a1b46b51d6ebc8a777de` |
| `C:\Users\deepe\.gstack\projects\dpa\learnings.jsonl` | [`learnings.jsonl`](mission-spine/supporting-artifacts/2026-08-15/learnings.jsonl) | 2116 | `d5aac911827eb2ba209e4713a764214bf0e8f6c76826158c854976a9f25a0a4c` |
| `C:\Users\deepe\.gstack\projects\dpa\unknown-reviews.jsonl` | [`review-summary.jsonl`](mission-spine/supporting-artifacts/2026-08-15/review-summary.jsonl) | 644 | `2b186128baa90077845552d5281f85b6099916eae458fc6f79e571a9cd28ea06` |
| `C:\Users\deepe\.gstack\projects\dpa\timeline.jsonl` | [`timeline.jsonl`](mission-spine/supporting-artifacts/2026-08-15/timeline.jsonl) | 1429 | `1597359724449dedd48fce918ad702cc8c699b2ad09938e3e195243a32205d54` |
| `C:\Users\deepe\.gstack\projects\DevPathAi-devpath-frontend\featmission-spine-et7-today-reviews.jsonl` | [`frontend-et7-review-log.jsonl`](mission-spine/supporting-artifacts/2026-08-15/frontend-et7-review-log.jsonl) | 2442 | `5cf1c42430a4e6a202cf805e3bee5245ba5915ac6f1ed14dda964c336333ac49` |
| `C:\Users\deepe\.gstack\projects\DevPathAi-devpath-frontend\featmission-spine-et9-mentor-context-reviews.jsonl` | [`frontend-et9-review-log.jsonl`](mission-spine/supporting-artifacts/2026-08-15/frontend-et9-review-log.jsonl) | 831 | `c3545feef44278a8e14d4cac2533e418a9634fd8651a30f8a1b4a1ccf2d1b7bb` |
| `C:\Users\deepe\.gstack\projects\DevPathAi-devpath-gitops\timeline.jsonl` | [`gitops-timeline.jsonl`](mission-spine/supporting-artifacts/2026-08-15/gitops-timeline.jsonl) | 1132 | `17b9276fe600cfd0a10fc0c7531fcc3c7b50c95cb69c392679a591f80868bb20` |
| `C:\Users\deepe\.gstack\projects\DevPathAi-devpath-shared\timeline.jsonl` | [`shared-timeline.jsonl`](mission-spine/supporting-artifacts/2026-08-15/shared-timeline.jsonl) | 148 | `6caa188fdc7ac39f0b9cd10434b0828ba86a2de4bacf1d2298c7ec8ecbe6bdf7` |
| `C:\Users\deepe\.gstack\projects\DevPathAi-devpath-lcs-svc\timeline.jsonl` | [`lcs-timeline.jsonl`](mission-spine/supporting-artifacts/2026-08-15/lcs-timeline.jsonl) | 135 | `f4bae028f97dd2a128690d5a41931e18d2ff5564f4e37441be8b2b01b8ec4581` |
| `C:\Users\deepe\.gstack\projects\DevPathAi-devpath-home-page\timeline.jsonl` | [`home-timeline.jsonl`](mission-spine/supporting-artifacts/2026-08-15/home-timeline.jsonl) | 620 | `1ad9966ec4187cd2e2f0b12220cfb5c398a6ad498f6f36436eb4738bd6e4a166` |
| `C:\Users\deepe\.gstack\projects\DevPathAi-devpath-frontend\timeline.jsonl` | [`frontend-timeline.jsonl`](mission-spine/supporting-artifacts/2026-08-15/frontend-timeline.jsonl) | 2077 | `66696f7187e6395c49ad4dc53123e1a4164593ecea2620cb5335d08d66418670` |

### 외부 중복 사본과 제외 범위

다음 임시 체크아웃 파일은 workspace 내부의 tracked 원본과 byte-for-byte 동일하므로 새 문서로 중복 편입하지 않았다.

| 외부 임시 사본 | workspace 내부 원본 | SHA-256 | 처리 |
|---|---|---|---|
| `C:\Users\deepe\AppData\Local\Temp\dpa-home-shallow-audit-0b7f79e50b4f4a449a7202c4fc41fbbe\HANDOFF.md` | `D:\workspace\dpa\devpath-home-page\HANDOFF.md` | `c735bb3c2f2878f8cf7e2ec051941702e27dad67e5a4049f81183a17d9bf4c7b` | 내부 원본 확인, 임시 clone 사본은 이동 대상 제외 |
| `C:\Users\deepe\AppData\Local\Temp\shared-et9-lf-0245cd774ccd41c4b087badab747a564\docs\superpowers\handoff-2026-06-13-image-pipeline.md` | `D:\workspace\dpa\devpath-shared\docs\superpowers\handoff-2026-06-13-image-pipeline.md` | `77dfab1f8838de5cf2bda605cfbf4f2c6cf6dc73ee894bf54de03ef1a3eec888` | 내부 원본 확인, 임시 clone 사본은 이동 대상 제외 |
| `C:\Users\deepe\AppData\Local\Temp\shared-et9-lf-0245cd774ccd41c4b087badab747a564\docs\superpowers\handoff-2026-06-13.md` | `D:\workspace\dpa\devpath-shared\docs\superpowers\handoff-2026-06-13.md` | `a1527af343b0d221a1daab54706b3f99cbc9a4f4696ae548541817741ca269a4` | 내부 원본 확인, 임시 clone 사본은 이동 대상 제외 |
| `C:\Users\deepe\AppData\Local\Temp\dpa-home-shallow-audit-0b7f79e50b4f4a449a7202c4fc41fbbe\src\analytics\mission-spine.analytics.v1.json` | `D:\workspace\dpa\.worktrees\home-et13-evidence\src\analytics\mission-spine.analytics.v1.json` | `486256fd212b96ea2fec0c6a95e22676b708989f94c0ca974276d6bd5f6b4908` | 코드 자산의 tracked clone 사본, 문서 이동 대상 제외 |

전역 `.gstack` 설정, `repo-mode.json`, 일반 analytics/tuning 로그, npm·Wrangler·BuildKit 캐시, build artifact, `__pycache__`, 비밀 파일은 문서가 아니므로 제외했다. 이동 후보 16개에는 private-key, GitHub token, cloud/provider key 패턴이 없음을 확인했다. 이동 후 `C:\Users\deepe\.gstack\projects`의 Markdown/JSON/JSONL에서 Mission Spine 식별자를 다시 검색했으며 남은 관련 문서는 0개였다.

## 11. 이월 작업 — 학습경로 생성(2026-08-14 핸드오프 이월)

원본은 documents 저장소의 [`docs/superpowers/handoff-2026-08-14-track-release-and-prod-issues.md`](https://github.com/DevPathAi/documents/blob/main/docs/superpowers/handoff-2026-08-14-track-release-and-prod-issues.md)다. 그 문서의 다음 착수점이 이 항목이며 2026-08-17 기준으로 여전히 미해결이다.

### 왜 이 릴리스 문서에 들어오는가

Mission Spine의 「현재 미션」 조회는 `learning_paths`(ACTIVE) → `path_milestones` → `path_weekly_tasks`를 읽는다(learning-svc `CurrentMissionQueryRepository`, 커밋 `fbd027c`). 운영 `learning_paths`는 **0행**이므로, Mission Spine 릴리스가 계약대로 끝나도 사용자 화면은 빈 상태로 남는다. 별건이 아니라 릴리스 가치의 선행 조건이다.

### 2026-08-14 문서의 전제 3건을 2026-08-17에 재측정해 정정한다

| 08-14 문서의 전제 | 2026-08-17 실측 | 근거 |
|---|---|---|
| GPU 쿼터 증설 요청이 PENDING | **승인 완료.** 요청 `33d1b4db…`는 `CASE_CLOSED`, 현재 G/VT On-Demand vCPU 쿼터 값 **4.0** | `aws service-quotas get-service-quota --service-code ec2 --quota-code L-DB2E81BA --region ap-northeast-2` |
| 현재 노드가 AZ 2d인데 GPU는 2a/b/c뿐이라 재프로비저닝 필요 | **g5.xlarge·g6.xlarge는 2d에 가용**하다. 2d에 없는 것은 g4dn뿐이다. 현재 노드 `i-09e252854566cc123`(t3.xlarge)도 2d다 | `aws ec2 describe-instance-type-offerings --location-type availability-zone` |
| (비용 미기재) | 서울 온디맨드 Linux 시간당 **g4dn.xlarge $0.647 · g6.xlarge $0.9896 · g5.xlarge $1.237** | AWS Price List API |

즉 GPU 경로를 막고 있던 이월 블로커 2개는 모두 사라졌다. 남은 것은 비용 판단이다.

### 결함은 세 겹이고, 08-14 문서가 지목한 것은 두 번째 층이다

08-14 문서는 「12분을 다 계산하고도 SSE가 끊긴 뒤라 저장에 도달하지 못한다」고 적었다. 코드 실측 결과 **먼저 터지는 것은 SSE가 아니라 타임아웃 체인**이고, SSE 폐기는 그것을 고친 다음에 마주치는 두 번째 결함이다. 세 결함 모두 실재한다.

- **D1 — 타임아웃 체인이 PT8S다.** learning-svc `devpath.ai-svc.timeout` 기본 `PT8S`, ai-svc `devpath.ollama.timeout` 기본 `PT8S`이고 gitops 매니페스트에 두 값의 override가 없다(`AI_SVC_TIMEOUT`·`OLLAMA_TIMEOUT` 미설정). 12분 생성과 양립할 수 없다. 2026-07-28의 PT600S 스톱갭은 원복된 상태다.
- **D2 — 계산이 끝난 뒤 폐기된다.** ai-svc는 Ollama를 `stream: false`로 호출하므로(`OllamaClient.callChat`) 서버는 응답 시점까지 아무것도 쓰지 않고, 따라서 클라이언트 이탈을 즉시 감지하지 못한 채 12분을 끝까지 계산한 뒤 죽은 소켓에 쓴다. learning-svc 쪽에는 별도의 폐기 경로가 하나 더 있다. `LearningPathController.send()`가 SSE `IOException`을 `IllegalStateException`으로 올리는데, 이 예외는 `matching` 단계(=AI 응답 수령 후 persist 이전)에서 던져질 수 있고 그러면 **완성된 결과가 저장되지 못한 채 버려진다**.
- **D3 — 중복 실행에 무방비다.** `/learning-paths/me/generate`는 요청마다 `CompletableFuture.runAsync`(공용 ForkJoinPool)로 새 생성을 띄운다. 12분짜리 작업이 클릭 수만큼 쌓이고, 그동안 Ollama가 노드 CPU 약 2코어를 점유해 다른 서비스까지 느려진다. `OllamaClient.generatePath`는 계약 위반 시 1회 재시도하므로 최악의 경우 비용이 2배다.

### 결정과 진행 방식(2026-08-17)

사용자 결정: **①먼저 비동기화(코드), ②하드웨어(Anthropic 크레딧 또는 GPU)는 그 다음.** 비동기화는 어느 하드웨어를 고르든 필요한 공통 기반이고, 결정 없이 착수할 수 있는 유일한 구간이다.

설계 제약 두 가지를 지킨다.

- **DB 마이그레이션을 추가하지 않는다.** 마이그레이션은 devpath-shared 중앙 Flyway에서만 실행되는데(learning-svc는 `spring.flyway.enabled=false`, `ddl-auto=validate`) shared는 이 문서의 HOLD 대상이다. 또 `learning_paths.status`의 CHECK는 `('ACTIVE','ARCHIVED')`뿐이라 생성 중 상태를 그 컬럼으로 표현할 수도 없다. 따라서 잡 상태는 learning-svc 프로세스 안에서 관리하고, 영속 결과는 기존 스키마 그대로 남긴다.
- **운영 반영은 HOLD를 따른다.** 이 작업의 산출물은 learning-svc `develop` 통합까지다. 타임아웃 override(`AI_SVC_TIMEOUT`·`OLLAMA_TIMEOUT`)를 포함한 gitops 매니페스트 변경과 이미지 배포는 Mission Spine HOLD가 풀린 뒤 릴리스 순서에 얹는다.

### 2026-08-17 진행 결과

비동기화 구간은 구현·검증을 마쳤고 세 저장소에 PR 로 올려 두었다.

| 저장소 | PR | 상태 | 내용 |
|---|---|---|---|
| learning-svc | [#52](https://github.com/DevPathAi/devpath-learning-svc/pull/52) | build **GREEN**(2m1s), develop 리뷰 대기 | 생성을 사용자당 하나의 작업으로 분리, 구독 실패가 생성을 중단시키지 못하게 하고, `GET /learning-paths/me/generation` 추가 |
| gitops | [#64](https://github.com/DevPathAi/devpath-gitops/pull/64) | **Draft/HOLD — 머지 금지** | `AI_SVC_TIMEOUT`·`OLLAMA_TIMEOUT` 을 `PT900S` 로. develop 에 넣으면 릴리스 PR #59 head 에 실리므로 HOLD 해제 후 처리 |
| documents | [#99](https://github.com/DevPathAi/documents/pull/99) | 리뷰 대기 | API 명세 §3.1·§3.2 |
| ai-svc | [#36](https://github.com/DevPathAi/devpath-ai-svc/pull/36) | 리뷰 대기 | 로컬 실측에서 드러난 결함 2건 — 학습경로만 영어 · 12주 계약 미검증 |

learning-svc 검증: `./gradlew build` 성공, 테스트 클래스 66·테스트 **245**(기존 234 + 신규 11)·실패 0·오류 0·스킵 0. `PathGenerationSurvivesDisconnectIT` 는 구독자 예외 보호를 임시 제거하면 red, 되돌리면 green 임을 확인해 **판별력을 실측**했다.

D1 은 gitops PR #64 가 HOLD 라 아직 운영에 적용되지 않는다. **즉 지금 배포해도 학습경로는 여전히 PT8S 에서 끊긴다.** D2·D3 만 코드로 닫혔다.

### 로컬 실측 — 목이 아니라 실제 Ollama 로 끝까지 돌렸다

learning-svc + ai-svc 를 로컬에 띄우고 실제 Ollama 로 생성해 세 가지를 확인했다. 운영 클러스터는 HOLD 라 건드리지 않았다.

- **비동기화가 실제로 동작한다.** 클라이언트를 생성 도중(20초)에 끊었는데도 작업이 계속돼 `state=SUCCEEDED, pathId=20` 으로 끝났고 DB 에 마일스톤 12개·태스크 36개가 남았다. `GET /learning-paths/me/generation` 도 `NONE → RUNNING → SUCCEEDED` 로 관측됐다. 종전 코드였다면 이 시점에서 결과가 버려졌다.
- **결함이 두 개 더 드러났다.** 목 테스트로는 보이지 않던 것들이다.
  - 학습경로만 **영어로 생성된다.** 멘토·커뮤니티 시드 프롬프트에는 `in Korean` 지시가 있는데 path 프롬프트에만 없었다.
  - **12주를 약속하고 3주를 저장한다.** qwen2.5:14b 가 weekNum 1·2·4 로 마일스톤 3개만 냈는데 계약이 통과시켰다. `LearningPathPersistenceService` 는 `total_weeks` 를 12로 하드코딩하므로 주차별 미션 화면이 3주차에서 빈다.
  - 둘 다 [AI PR #36](https://github.com/DevPathAi/devpath-ai-svc/pull/36) 에서 프롬프트·스키마·검증으로 닫았다. 수정 후 운영과 동일한 qwen2.5:3b 로 재측정해 **주차 1~12 완전 커버, 한국어 제목 12/12, 33초**(로컬 GPU)를 확인했다.
- **하드웨어 판단의 전제가 하나 바뀌었다.** 08-14 문서는 「모델 축소는 품질이 떨어지고 3b 로는 계약을 만족하는 JSON 이 안 나올 위험」이라고 적었는데, 프롬프트를 고치자 **3b 가 강화된 계약(12주·한국어)을 만족했다.** 품질만 놓고 보면 Claude 가 필수는 아니다. 다만 로컬 측정은 GPU 기준이므로 **운영 CPU 의 12분이라는 사실은 그대로다** — 이 관측은 「GPU 로 옮기면 작은 모델로도 쓸 만하다」는 근거이지 「CPU 로도 된다」는 근거가 아니다.

남은 일은 다음과 같다.

- **하드웨어 결정** — Anthropic 크레딧 충전(ai-svc 에 path 용 Claude 클라이언트 신규 구현 필요) 또는 GPU 노드(g6.xlarge, 2d 가용, 쿼터 승인됨). 비동기화만으로는 생성이 12분 걸리는 사실은 바뀌지 않는다.
- **프론트엔드 폴링** — 웹은 아직 SSE 단절을 실패로 처리하고 `GET /me/generation` 을 부르지 않는다. 지금 손대면 릴리스 PR #133 의 head 가 움직이므로 HOLD 해제 뒤에 붙인다.
- **ai-svc 재시도 정책** — `OllamaClient.generatePath` 는 계약 위반 시 1회 재시도한다. 3b 모델에서 계약 실패가 나면 최악 24분이 된다.

### 함께 이월된 나머지(08-14 문서 §3)

- 사용자 육안 확인 3건 — 자유글·피드백 작성, 문의 전송, AI 멘토 응답. 셋 다 8/14에 배포됐으나 인증 게이트 때문에 자동 검증이 불가능해 미확인 상태다.
- leva.ai.kr SEO — `templates/note.html` CTA 블록, title·description의 「레바」 반영, 폴백 문구 정정, GSC 등록(사용자 몫).
- 남은 두 트랙 `NODE_TYPESCRIPT`·`DATA_AI` — 트랙 CHECK는 이미 8값으로 열려 있다. 착수 전 `generateContentsLocal`이 30개를 한 번에 내는지 먼저 끝까지 돌려볼 것.
- 글 수정·삭제 — community-svc에 `@PutMapping`·`@PatchMapping`·`@DeleteMapping`이 0건이라 신규 개발 건이다.
