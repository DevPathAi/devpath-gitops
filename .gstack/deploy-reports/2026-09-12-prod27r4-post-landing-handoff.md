# Mission Spine prod27r4 post-Landing handoff

- 작성 시각: `2026-09-12T19:23:55.7815712+09:00`
- 릴리스 ID: `ms-20260909-prod27r4`
- 상태: `DONE_WITH_CONCERNS`
- 배포 상태: production mission-ON, staging mission-ON rebaseline, Cloudflare Landing-last 완료
- 현재 GitOps main: `6c16aadea28a0818854ac761d11c019415572096`
- sealed candidate: `release/candidate-ms-20260909-prod27r4@801bf75f47e5c8af67627a3a9ab6fc1f72b414a9`
- Cloudflare production deployment: `4c86f082-834d-4154-84e0-b0016725c97a`
- 남은 우려: 노출된 구형 classic `ghp_` PAT의 GitHub 측 revoke 증거가 없음

## 최종 결과

prod27r4는 production과 Home Landing까지 완료됐다. 보호된 main의 최신 release control commit에서 새 900초 production canary를 통과했고, staging은 이미 mission-ON인 상태를 멱등적으로 검증·재기준화했다. Landing은 앞선 실패 run이 이미 만든 정확한 Cloudflare 배포를 `mode=reuse`로 채택했으며 새 배포를 중복 생성하지 않았다.

성공 Landing 뒤 GitHub 환경의 임시 `CLOUDFLARE_API_TOKEN`은 즉시 삭제했다. 로컬에서 Cloudflare current-deployment CAS, 불변 public marker, Home smoke를 독립적으로 다시 실행해 통과를 확인했다.

## 이번 연속 작업에서 반영한 release-control 수정

### staging 재기준화 멱등성

- `fix(release): make staging rebaseline idempotent`
- protected main commit: `e1758b8a0c9bc678c63354e432a371e6070401f3`
- staging이 이미 정확한 prod27r4 mission-ON 상태라면 봉인된 prior를 다시 요구하지 않고 현재 상태를 검증한다.
- 성공 run에서 `Identify the exact idempotent staging rebaseline state`와 `Rebaseline staging to the exact promoted mission-ON lineage`가 통과했고 prior 검사는 의도대로 skip됐다.

### Cloudflare Pages 배포 목록 page size

- `fix(release): use supported Pages deployment page size`
- protected main commit: `6c16aadea28a0818854ac761d11c019415572096`
- live Cloudflare Pages API에서 `per_page=100`과 `50`은 HTTP 400, `25`는 HTTP 200이었다.
- `DEPLOYMENT_PAGE_SIZE=25`로 고정했고, 모든 페이지의 정확한 요청 URL을 검증하는 테스트를 추가했다.
- release hardening, Cloudflare API, Landing evidence, workflow 테스트 묶음은 `45 tests / 55 subtests`를 통과했다.
- sealed promotion chain은 pagination fix가 staging idempotency fix를 정확히 이어받고 phase `mission-on`을 유지함을 검증했다.

### develop과 protected main 경로

- pagination implementation PR #158: `https://github.com/DevPathAi/devpath-gitops/pull/158`
  - develop merge: `514ee57f987933e8bb9f58c12eefa3f4bac3cd20`
- pagination chain registration PR #159: `https://github.com/DevPathAi/devpath-gitops/pull/159`
  - develop merge: `988661b0bfb070e6dc423397c3178aad35fa8036`
- exact protected-main target: `6c16aadea28a0818854ac761d11c019415572096`
- one-shot publisher helper: `f4727720638c473c1daf34fd6150125f06688e7f`
- publisher run `34686584754`: success
- main push CI `34686639015`: success
- 임시 publisher branch environment policy는 run 승인 뒤 제거했고 환경은 `main` only로 복구했다.

## promotion 최종 증거

- workflow: `Mission Spine - production exact digest promotion`
- run: `https://github.com/DevPathAi/devpath-gitops/actions/runs/34686696557`
- run ID / attempt: `34686696557 / 1`
- head SHA: `6c16aadea28a0818854ac761d11c019415572096`
- conclusion: `success`
- production job `103534830423`: success
  - exact mission-ON 생성/재사용 검증
  - 900초 canary hold 성공
  - 9개 서비스 사후 런타임 재관측 성공
  - canary evidence artifact 업로드 성공
- staging job `103537188496`: success
  - already mission-ON 상태 식별 성공
  - sealed prior 검사 skip
  - 정확한 mission-ON lineage 재기준화 성공
- canary artifact:
  - ID: `10295454490`
  - name: `ms-20260909-prod27r4-production-canary-run-34686696557-attempt-1`

## Landing-last 최종 증거

- workflow: `Mission Spine - Landing last`
- run: `https://github.com/DevPathAi/devpath-gitops/actions/runs/34687746592`
- run ID / attempt: `34687746592 / 1`
- head SHA: `6c16aadea28a0818854ac761d11c019415572096`
- conclusion: `success`
- job `103537610971`: success, `1m13s`
- canary binding: run `34686696557`, attempt `1`, exact current-main head
- Cloudflare preflight: `verified Landing candidate and exact production CAS mode=reuse`
- selected deployment ID: `4c86f082-834d-4154-84e0-b0016725c97a`
- post-selection verification: exact current-deployment CAS, public marker, Home smoke 성공
- evidence 직전 Cloudflare 재검증: 성공
- sanitized evidence artifact:
  - ID: `10296118125`
  - name: `ms-20260909-prod27r4-landing-last-run-34687746592-attempt-1`
  - `evidence.json` SHA-256: `6F601AD4D248D2E2F295A0BA0F04CF731B04369CCE4DC8519F5B9E22585D6428`
  - status: `passed`
  - producer run/attempt: `34687746592 / 1`
  - approver: `VelkaressiaBlutkrone`

## Cloudflare 최종 상태

- project: `devpath-home-page`
- production deployment ID: `4c86f082-834d-4154-84e0-b0016725c97a`
- created at: `2026-09-12T09:21:07.410545Z`
- environment/status: `production / success`
- exact Home source SHA: `ad34325755c1aadceeb60d63ea08114b82c5ab4c`
- canonical Home SHA-256 / marker key: `7cb9159501e33873498139968ede36d44fc7be250613673b5747ba58c6689071`
- 앞선 run `34685542491`은 Wrangler deploy를 성공시켜 이 배포를 만들었지만, unsupported `per_page=100` 목록 조회 때문에 capture 단계에서 실패했다.
- 최종 run `34687746592`은 고친 pagination으로 이 배포를 정확히 찾아 재사용했고 Cloudflare write를 반복하지 않았다.

## 봉인된 release 좌표

- candidate commit: `801bf75f47e5c8af67627a3a9ab6fc1f72b414a9`
- candidate spec SHA-256: `3f8952bdc60faeb1b4da5971a356fa4a33082fdf6155383f8b96805e0fad0cd3`
- release manifest SHA-256: `e6a4d6f187c5a503ceeaf68fd1899f5a4f147f43dd6d571201d7354ee9261e00`
- migration commit: `a5a6263f33a023ee11269c8f2d000758fce7b22c`
- services commit: `12fd3b9be4d095e391813f618b95fe9fe42a7416`
- mission-ON control commit: `6c16aadea28a0818854ac761d11c019415572096`

## 보호 상태와 secret 정리

다음 5개 환경을 Landing 뒤 다시 조회했다.

- `mission-spine-production-off`
- `mission-spine-production-on`
- `mission-spine-production-landing`
- `mission-spine-production-rollback`
- `mission-spine-staging`

모두 다음 상태다.

- deployment branch policy: `main` only
- required reviewer: `VelkaressiaBlutkrone`
- `prevent_self_review=true`
- `can_admins_bypass=false`

Landing 환경 secret은 현재 다음 두 개뿐이다.

- `GITOPS_RELEASE_APP_ID`
- `GITOPS_RELEASE_APP_PRIVATE_KEY`

임시 `CLOUDFLARE_API_TOKEN`은 없다. credential 값, Kubernetes Secret data, PAT 값은 출력하거나 문서화하지 않았다.

## 남은 작업과 차단 증거

### 구형 classic PAT revoke

구형 classic `ghp_` PAT는 cluster 사용 경로에서 제거됐지만 GitHub 측 revoke는 아직 증명되지 않았다. 이번 세션에서 Computer Use를 실제로 초기화했으나 `cua.getState()`는 apps와 browsers를 모두 빈 배열로 반환했고, GitHub token settings URL에 대해 브라우저를 고르는 호출은 `No browser is available`로 실패했다.

다음 세션은 Chrome, Edge 또는 in-app browser surface가 연결된 뒤 다음 순서로 처리한다.

1. `https://github.com/settings/tokens`에서 exact legacy token을 이름/상태로 식별한다. 토큰 값은 읽거나 복사하지 않는다.
2. revoke/delete 최종 클릭 직전에 irreversible deletion임을 밝히고 명시적 destructive confirmation을 받는다.
3. 확인 후 exact token 하나만 폐기한다.
4. 설정 목록을 새로 읽어 해당 token이 사라졌음을 증거로 남긴다.
5. 로그인, 비밀번호, 2FA, recovery code, password manager 입력이 필요하면 자동화하지 않고 그 지점에서 중단한다.

현재 `gh` 로그인은 keyring의 `VelkaressiaBlutkrone` 계정이며 `admin:org`, `gist`, `repo`, `workflow`, `write:packages` scopes를 보고한다. 이 메타데이터만으로 폐기 대상을 특정하지 않는다.

### Temp 정리

다음 두 경로는 모두 `C:\Users\deepe\AppData\Local\Temp\` 바로 아래임을 절대 경로로 검증했다.

- `C:\Users\deepe\AppData\Local\Temp\prod27r4-wrangler-bdd86c057ceb41bca7778dd53d6d4baa`
- `C:\Users\deepe\AppData\Local\Temp\prod27r4-landing-evidence-7451d0cb647b4664b843457b2e166269`

PowerShell native `Remove-Item -LiteralPath <exact-path> -Recurse -Force`는 권한 분류기에서 실행 전 두 번 거부됐다. 동일 호출을 반복하지 않는다. 정책이 허용되는 세션에서 AI가 exact 경로를 다시 검증한 뒤 정리한다.

## 작업 트리와 체크포인트

prod27r4에 직접 사용한 다음 worktree는 저장 시 clean이었다.

- `D:\workspace\dpa\.worktrees\gitops-prod27r4-candidate`
- `D:\workspace\dpa\.worktrees\gitops-landing-wrangler-trust-main-20260911`
- `D:\workspace\dpa\.worktrees\gitops-prod27r4-rebaseline-publisher`

워크스페이스의 다른 worktree에는 기존 사용자/다른 작업 변경이 있으므로 정리하거나 수정하지 않는다.

최종 append-only checkpoint:

`C:/Users/deepe/.gstack/projects/DevPathAi-devpath-gitops/checkpoints/20260912-192011-prod27r4-landing-completed-handoff.md`

checkpoint SHA-256:

`7025DC7F1F1A43D69A9158AB9A5C20F85E5C37E8E1E9818F9D3F76C2543DAF33`

다음 세션 시작 명령:

```text
$context-restore C:/Users/deepe/.gstack/projects/DevPathAi-devpath-gitops/checkpoints/20260912-192011-prod27r4-landing-completed-handoff.md
```
