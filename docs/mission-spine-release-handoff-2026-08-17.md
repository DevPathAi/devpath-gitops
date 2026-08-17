# Mission Spine 릴리스 핸드오프 — 2026-08-17

## 1. 목적과 현재 판정

이 문서는 Mission Spine 릴리스 코드와 producer 변경을 다음 운영자에게 안전하게 넘기기 위한 인수 문서다. 기준일은 2026-08-17(KST)이다.

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
| Frontend evidence-reader 분리 | [Frontend PR #134](https://github.com/DevPathAi/devpath-frontend/pull/134), head `8d2c5279749295868b77dd1294fbbbd023d7ba09` | exact-head CI 성공 후 merge commit으로 `develop`에 병합 |
| Frontend 릴리스 | [Frontend PR #133](https://github.com/DevPathAi/devpath-frontend/pull/133) | #134 병합으로 갱신된 `develop` SHA에서 전 체크 재실행 후 독립 승인 필요 |
| Shared 릴리스 | [Shared PR #67](https://github.com/DevPathAi/devpath-shared/pull/67) | build 성공, 독립 reviewer 승인 필요 |
| GitOps release control | `feat/mission-spine-et13-evidence` | 이 문서를 포함한 PR을 `develop`에 정상 병합한 뒤 최종 producer 재바인드 필요 |

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

1. Frontend PR #134의 exact-head CI를 모두 GREEN으로 만든 뒤 merge commit으로 `develop`에 병합한다.
2. Frontend PR #133이 새 develop head를 가리키는지 확인하고 CI·독립 review를 다시 받는다. 아직 main에 병합하지 않는다.
3. GitOps feature PR을 `develop`에 merge commit으로 병합한다. main release PR은 producer final SHA가 확정될 때까지 유지한다.
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

- [ ] Frontend #134 exact-head CI GREEN 및 develop merge commit 기록
- [ ] Frontend #133 final develop SHA/CI/reviewer 기록
- [ ] GitOps feature→develop 및 develop→main PR SHA 기록
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

이 체크리스트가 모두 채워지기 전에는 “릴리스 완료” 또는 “production GO”로 표시하지 않는다.
