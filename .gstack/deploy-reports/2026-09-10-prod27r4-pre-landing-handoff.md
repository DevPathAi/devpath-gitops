# Mission Spine prod27r4 pre-landing handoff

- 작성 시각: `2026-09-10T21:40:00+09:00`
- 릴리스 ID: `ms-20260909-prod27r4`
- 상태: production mission-ON 완료, staging prior 복구 완료, Cloudflare Landing 미실행
- 현재 GitOps main: `ce21141303f05c24209b5de7cb52bae271a66e0d`
- sealed candidate branch: `release/candidate-ms-20260909-prod27r4`
- sealed candidate commit: `801bf75f47e5c8af67627a3a9ab6fc1f72b414a9`
- candidate spec SHA-256: `3f8952bdc60faeb1b4da5971a356fa4a33082fdf6155383f8b96805e0fad0cd3`
- release manifest SHA-256: `e6a4d6f187c5a503ceeaf68fd1899f5a4f147f43dd6d571201d7354ee9261e00`

## 완료된 작업

### 후보와 선행 증거

- ET13 baseline run `34352698836`: 성공.
- Signed Android run `34352836581`: 성공.
- Home deterministic build: 성공.
  - canonical SHA-256: `7cb9159501e33873498139968ede36d44fc7be250613673b5747ba58c6689071`
- prod27r4 candidate와 release manifest가 봉인됐고 candidate branch는 원격에 있다.

### production migration, services, mission-ON

- migration commit: `a5a6263f33a023ee11269c8f2d000758fce7b22c`
- writer-fence validator fix: `f619d645e3d1e9f8040efddcdab7b6c74e6c606a`
- services commit: `12fd3b9be4d095e391813f618b95fe9fe42a7416`
- mission-OFF commit: `5e25b46322faaf8602f16625ac169a589f77883e`
- initial mission-ON commit: `d117b476d58a8f34cea94e9ac2472e673144ea1c`
- Landing direct-upload parser fix/current mission-ON commit: `ce21141303f05c24209b5de7cb52bae271a66e0d`
- clean migration Job `devpath-flyway-migrate-81029e190726-e6a4d6f187c5a503ceeaf68f`는 성공했고 Flyway target `202609051004`가 확인됐다.
- 9개 서비스 런타임과 writer fence `false`가 확인됐다.
- promotion run `34464689070`은 initial ON에 대해 production 900초 canary와 staging rebaseline까지 성공했다.
- current main `ce211413...`에 대한 promotion run `34474167778`의 production-on job은 성공했다.
  - production-on job `102860907454`
  - exact current-main 900초 canary 성공
  - canary artifact 업로드 성공
  - 9개 서비스 최종 런타임 재관측 성공

### staging 재개 문제와 복구

- run `34474167778` 전체 결론은 `failure`다. production 실패가 아니라 staging rebaseline의 비멱등 prior 검사 때문이다.
- staging은 이전 성공 run `34464689070` 이후 이미 prod27r4 mission-ON 상태였다. 재실행 workflow가 먼저 봉인된 prior를 300초 동안 요구해 job `102867444975`가 다음 오류로 끝났다.
  - `Deployment image is not an allowed sealed reference`
- GitHub 환경 비밀을 복사하지 않고 기존 AWS 관리자 자격과 등록된 SSH 키로 k3s에 접속했다.
- `stage_web_release.py`의 `build_cas_patch`를 사용해 현재 exact mission-ON 모양과 resourceVersion `3301946`을 검증한 뒤 staging Deployment만 prior로 CAS 패치했다.
- 복구 후 상태:
  - namespace/deployment: `devpath-staging/devpath-web-staging`
  - generation/observedGeneration: `129/129`
  - Ready/Available replicas: `1/1`
  - prior release ID: `ms-20260830-prod26r9`
  - prior candidate SHA-256: `f86f010532dff3d231528f5ba4bc9b128704b9582393415b520ab981f7ad9ca5`
  - Deployment image와 실제 Ready Pod imageID: `ghcr.io/devpathai/devpath-web@sha256:278b1a862c5a9750f67cb1089f70ddba97fd1260af8e3ef3c95d0c1a3f16cc21`
- production과 GitOps main은 이 복구에서 변경하지 않았다.

### Cloudflare Landing 진단과 수정

- 최초 Landing run `34466709761`은 `mission-spine-production-landing`의 `CLOUDFLARE_API_TOKEN`이 비어 있어 Cloudflare write 전에 실패했다.
- Cloudflare Pages project `devpath-home-page`는 direct-upload 프로젝트다. live API는 `source: null`이 아니라 `source` 키 자체를 생략한다.
- `scripts/release/cloudflare_pages.py`가 생략된 `source`를 direct-upload로 받도록 test-first 수정했다.
- fix branch/commit:
  - `fix/prod27r4-cloudflare-direct-upload-source-20260910`
  - `ce21141303f05c24209b5de7cb52bae271a66e0d`
- full release suite: `325 tests`, `OK (skipped=3)`.
- refreshed Wrangler OAuth로 live preflight가 두 번 성공했다.
- 현재 canonical Cloudflare production deployment는 여전히 prior `40b09238-1f1f-41e3-8728-75dbfd725f3f`다. prod27r4 Landing write는 아직 실행하지 않았다.
- landing environment에는 현재 `GITOPS_RELEASE_APP_ID`, `GITOPS_RELEASE_APP_PRIVATE_KEY`만 있고 `CLOUDFLARE_API_TOKEN`은 저장돼 있지 않다.

## 현재 보호 상태

- 다음 환경은 모두 `prevent_self_review=true`, reviewer `VelkaressiaBlutkrone`, deployment branch `main` only로 복구돼 있다.
  - `mission-spine-production-off`
  - `mission-spine-production-on`
  - `mission-spine-staging`
  - `mission-spine-production-landing`
- `origin/main`과 fix branch는 모두 `ce21141303f05c24209b5de7cb52bae271a66e0d`다.
- promotion chain verifier는 `ce211413...`에서 성공한다. phase는 `mission-on`이고 writer fence는 `false`다.

## 다음 세션 우선순위

1. 이 문서와 append-only checkpoint를 `$context-restore`로 복원한다.
2. `origin/main=ce211413...`, candidate branch `801bf75...`, staging prior digest `278b1a...`를 읽기 전용으로 재확인한다.
3. main에서 promotion workflow를 새 attempt-one run으로 dispatch한다.

   ```powershell
   gh workflow run mission-spine-promote.yml --repo DevPathAi/devpath-gitops --ref main -f release_id=ms-20260909-prod27r4
   ```

4. production-on과 staging 보호 환경을 정확한 pending job에 승인한다. production은 다시 900초 current-main canary를 수행한다. staging은 이번에는 prior CAS를 통과해 prod27r4 mission-ON으로 재기준화돼야 한다.
5. promotion run 전체가 `success`, attempt `1`, head SHA `ce211413...`인지 확인한다. 실패 run `34474167778`의 canary artifact만으로 Landing을 진행하지 않는다.
6. pinned Wrangler `4.123.0`의 `whoami`로 로컬 OAuth를 갱신하고, 토큰 값을 출력하지 않은 채 `mission-spine-production-landing`에 `CLOUDFLARE_API_TOKEN`을 임시 저장한다.
7. `mission-spine-landing-last.yml`을 main에서 dispatch하고 정확한 Landing pending job을 승인한다.

   ```powershell
   gh workflow run mission-spine-landing-last.yml --repo DevPathAi/devpath-gitops --ref main -f release_id=ms-20260909-prod27r4
   ```

8. Landing 성공/실패와 무관하게 즉시 임시 secret을 삭제하고 이름이 사라졌는지 확인한다.

   ```powershell
   gh secret delete CLOUDFLARE_API_TOKEN --repo DevPathAi/devpath-gitops --env mission-spine-production-landing
   gh secret list --repo DevPathAi/devpath-gitops --env mission-spine-production-landing
   ```

9. Landing evidence, Cloudflare current production deployment ID, marker/smoke/CAS를 확인하고 최종 release handoff를 작성한다.

## 보안 및 주의사항

- 과거 cluster `ghcr-pull` Secret에 있던 노출된 classic `ghp_` PAT는 기존 GitHub CLI OAuth 토큰으로 교체됐다. Kubernetes Secret의 `.data`나 credential 값을 출력하지 않는다.
- 노출된 classic PAT의 GitHub 측 revoke는 아직 완료되지 않았다. 승인 후 Computer Use를 실제 시도했지만 사용 가능한 browser/app surface가 없어 차단됐다. 브라우저가 생기면 GitHub token settings에서 해당 PAT를 폐기한다. 인증 비밀번호나 2FA를 자동화하지 않는다.
- 로컬 Wrangler credential 파일과 GitHub/Kubernetes secret 값을 문서, 로그, 명령 인수에 출력하지 않는다.
- `CLOUDFLARE_API_TOKEN` 임시 저장과 성공/실패 후 삭제는 사용자가 승인했다. secret 체류 시간을 최소화한다.
- protected `main`에 새 commit을 올리거나 병합해야 하는 상황이 생기면 별도 사용자 승인을 다시 받는다.
- local Docker volume/DB는 정리하지 않는다.

## 주요 worktree

- sealed candidate: `D:\workspace\dpa\.worktrees\gitops-prod27r4-candidate`
- current-main parser fix: `D:\workspace\dpa\.worktrees\gitops-prod27r4-cloudflare-direct-upload-fix`
- parser-fix publisher: `D:\workspace\dpa\.worktrees\gitops-prod27r4-cloudflare-direct-upload-publish`
- handoff docs: `D:\workspace\dpa\.worktrees\gitops-prod27-handoff-20260909`

## append-only checkpoint

`C:/Users/deepe/.gstack/projects/DevPathAi-devpath-gitops/checkpoints/20260910-213841-prod27r4-pre-landing-release-handoff.md`

다음 세션 시작 명령:

```text
$context-restore C:/Users/deepe/.gstack/projects/DevPathAi-devpath-gitops/checkpoints/20260910-213841-prod27r4-pre-landing-release-handoff.md
```
