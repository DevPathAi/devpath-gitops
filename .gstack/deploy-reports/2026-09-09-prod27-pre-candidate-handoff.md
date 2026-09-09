# Mission Spine prod27 pre-candidate handoff

- 작성 시각: 2026-09-09T21:57:52+09:00
- 릴리스 ID: `ms-20260909-prod27`
- GitOps 보호 기준: `e7c32930ee42061075283a9893bd46247f16868d`
- 문서 브랜치: `docs/prod27-pre-candidate-handoff-20260909`
- 상태: pre-candidate 선행 증거 완료, 후보 조립 전 이관

## 이번 세션 완료

### GitOps main 병합과 거버넌스

- PR #146을 사용자 승인 후 SHA 고정 squash merge했다.
  - PR: <https://github.com/DevPathAi/devpath-gitops/pull/146>
  - 승인된 head: `a2b0401ca504491ed45012a5770584e0614c8086`
  - merge commit: `8f25527c7efced270fc1875b23601410cbb00124`
- mentor Shared migration release 계약 불일치를 수정했다.
  - develop PR #147: <https://github.com/DevPathAi/devpath-gitops/pull/147>
  - develop merge: `f6879ea0585315c08dec757a640b1ce05c0f9caf`
  - main PR #148: <https://github.com/DevPathAi/devpath-gitops/pull/148>
  - 승인된 head: `2027c9a3afb3f47d431352b283d1c6287f3754b0`
  - main merge: `e7c32930ee42061075283a9893bd46247f16868d`
- PR #148 병합 후 main push CI run `34351080704`의 release contract와 Kustomize job이 모두 성공했다.
- main ruleset을 임시 해제한 트랜잭션은 `finally`에서 정확히 복구됐다.
  - `mission-spine-main-governance` ruleset `21194270`: active
  - `mission-spine-main-integrity` ruleset `21194269`: active
  - branch protection `enforce_admins=true`
  - required approving reviews: `1`
- `origin/main`은 merge commit `e7c32930ee42061075283a9893bd46247f16868d`와 일치한다.

### mentor Shared release 계약

- source: `9793b8f92f92cca1ef57e28d2db6fb7d911741a3`
- version: `0.0.1-rm.20260907`
- JAR SHA-256: `3a64de1a1773f1aa05ccd801a88f01ef2cead887e44930554074230fd01f2996`
- migration image: `sha256:81029e190726c7967a6c840caee1735586da3a694081982b29261b2a54436e2b`
- Flyway target: `202609051004`
- required migration: `V202609051004__mentor_invite_batches.sql`

### Frontend ET13 승인 베이스라인

- protected main/source: `727de5a272269b64e36826f7de704d7868f24590`
- 원본 diagnostic run: `34226991033`, attempt `1`
- 원본 artifact: `10056455900`
- 원본 artifact digest: `sha256:0f2d40295490608b45d6c7b2b73f04ae88691d07070e72c5525b69df2757144a`
- 원본 workflow SHA-256: `001654acab3847e3cdce750e84cf18315b8853f60a43c1289a34a14ae939843f`
- 새 baseline approval run: `34352698836`, 성공
- 승인 환경: `et13-baseline-approval`
- 승인 계정: `VelkaressiaBlutkrone` (`77432570`)
- 승인 후 `restored_prevent_self_review=true`
- 승인 artifact: `10104442661`
- artifact name: `ms-20260909-prod27-frontend-visual-approved-baseline-run-34352698836-attempt-1`
- artifact digest: `sha256:9cf8714872b416b2fc101f30baeba43a80788e1cef0286ba29b7b872d5d25420`
- `baseline-approval.v1.json` SHA-256: `1da1da14ff264feff48445cc592b98af42745034a246637d7a05f6aced508a7a`
- approved baseline set: `ac27115333e40ae1ac7eb150e7948db20bfa348b25752a1669014f66a6b992f9`
- case catalog: `1f21427cec099ba1d5465d0207fd3f8261084d1e69d94827f02fa44341272e1b`
- projection contract: `c66d08b6425628a06b27d07e08d648cfb3568d9db7c8d8aca2371172ccf4bde3`
- 로컬 추출 경로: `D:\workspace\dpa\.release-artifacts\ms-20260909-prod27\frontend-baseline`

### Frontend 서명 Android

- workflow run: `34352836581`, attempt `1`, 성공
- protected environment: `mission-spine-mobile-signing-android`
- 승인 계정: `VelkaressiaBlutkrone` (`77432570`)
- 승인 후 `restored_prevent_self_review=true`
- 최종 artifact: `10104714267`
- artifact name: `ms-20260909-prod27-signed-android-build-run-34352836581-attempt-1`
- artifact archive digest: `sha256:e185b27d916643449bca2763ec701427dce46fe299c79827e1240c099ceb2d5c`
- workflow SHA-256: `65f5725b56b3be919199b922276d4b367d87cb2d3e6f7ccf3e179de3abc7b4ca`
- `build-provenance.v2.json` SHA-256: `9a289374e61575b03f198e14f18361e3281db90ab47090902d2c446a3e97300c`
- signed APK SHA-256: `e49d89465268b056a5466eb81c5bd9c84fc6ecca1254c4df557c1f9dfef9dcb8`
- APK bytes: `73270988`
- signature verification: `true`
- signing classification: `org_keystore_release_test_distribution`
- 로컬 추출 경로: `D:\workspace\dpa\.release-artifacts\ms-20260909-prod27\signed-android`

### 현재 보호 브랜치와 GHCR 좌표 재인증

| Component | Protected source | GHCR digest |
| --- | --- | --- |
| web mission-off | `727de5a272269b64e36826f7de704d7868f24590` | `sha256:3b64bca63efcb08687e78e1a5dc8a93ce20f6fd29dabc70df7af5d57bd333fe1` |
| web mission-on | `727de5a272269b64e36826f7de704d7868f24590` | `sha256:a0b0f1bec7f1e4a378ee656eca59df48097c8423344633da0aa7263378369547` |
| admin | `727de5a272269b64e36826f7de704d7868f24590` | `sha256:202ffbf76c9521713811cf615f16d0f343d60b16236f7e804529ab1cf6c64bb8` |
| AI | `54f634b845befc7085e4b974a8b66120bf6c8856` | `sha256:eb6f3c2baab60d3d36d6c8cd67d8114322038d8df4ac910bb4a9824907ff83d0` |
| gateway | `44f22a3a684d6c81ffcfd8327cfc47adb8613729` | `sha256:a5bdfbaf56aa8add46f3f8ca1f982252e2b06d13402d7e3c3c0e60669a5fd94a` |
| platform | `cd4c1317f328c52e481cefc219a467d2227ae968` | `sha256:29bcc284b42faa8c936eef146a6587c614ad87ba60d3e0d8b0bf2e2336a10494` |
| community | `f4f05edbfaabf4b4cdc879b0433fd4507e1d7bfe` | `sha256:500fab3b728e67576ac677a3e20bf45f77d4f071cf7a8537ffdf93a6c200a94d` |
| LCS | `de767a0c397f18e2e4e3a118cc0dbd27c2669812` | `sha256:dceb15ca75f086406d3b2fe63c5dbf0a8d6abd621e645a8a782bfe0857b31a60` |
| learning | `c87adf43451d9179059620bc16a48503fa9dff3b` | `sha256:4e96666b341584e4081daca69a3499b82ee1dd84126bbf47c0386cd6b2ea4275` |
| notification | `c659b215e76d8d59eed772df31d81d9b3e7a9f63` | `sha256:3694114c08ac74468043b6e37eac01ce555031ab86d341d4039c4625a0ffc2ba` |
| sandbox | `990aacb2d1c17e794ed58133a054c93178eab90d` | `sha256:896267c2756d795b7c263af5da6c201900787ab6a932df2eab37ebaea7b07d0d` |
| migration | `9793b8f92f92cca1ef57e28d2db6fb7d911741a3` | `sha256:81029e190726c7967a6c840caee1735586da3a694081982b29261b2a54436e2b` |

각 protected branch SHA와 GHCR SHA-tag가 정확히 하나의 package version/digest에 매칭됨을 GitHub API로 확인했다.

### Home 로컬 선행 빌드

- protected master: `9a2379ff86ad835fab4426ef786e5ebd1e5eab68`
- detached worktree: `D:\workspace\dpa\.worktrees\home-prod27-preview`
- Node `v24.12.0`, npm `11.6.2`
- `npm ci`와 `npm run build` 성공
- canonical `dist.tar.gz` SHA-256 계산값: `7cb9159501e33873498139968ede36d44fc7be250613673b5747ba58c6689071`
- canonical archive bytes: `565311`
- worktree tracked 상태: clean
- Cloudflare candidate preview는 배포하지 않았다. 따라서 외부 Home 상태 변경은 없다.
- `npm ci`는 현재 lockfile 기준 7개 취약점(중간 4, 높음 2, 치명적 1)을 보고했다. 이 세션에서는 dependency 변경을 하지 않았다.

## 현재 운영 상태

- Argo CD `devpath-migration`은 GitOps `e7c32930...`에 `Synced / Suspended` 상태다. inert base이므로 예상된 상태다.
- 다른 application은 manifest 변경이 없어 기존 적용 revision에 유지됐다.
- 기존 `devpath-ollama-gpu Progressing` 외 새로운 health 저하는 관측되지 않았다.
- 이번 세션에서는 candidate spec 생성, GitOps candidate workflow dispatch, Cloudflare Pages preview 배포, staging/production promotion을 실행하지 않았다.

## 다음 세션 우선순위

1. 이 문서와 append-only checkpoint를 복원하고 GitHub 계정이 `VelkaressiaBlutkrone`, GitOps `origin/main`이 `e7c32930...`인지 확인한다.
2. Home `9a2379ff...` 빌드의 canonical dist 해시 `7cb915...`를 독립 재현한 뒤, `devpath-home-page` 프로젝트에 non-production branch preview를 배포한다. 실제 `candidate_deployment_id`와 현재 `prior_production_deployment_id`를 Cloudflare API로 인증한다.
3. Home visual/a11y catalog의 current rendered-product provenance, tree hash, provenance hash, font manifest hash를 exact source bytes로 계산한다.
4. AI release eval의 current rendered config SHA-256과 Ollama endpoint SHA-256을 비밀값을 출력하지 않고 재인증한다.
5. `ms-20260909-prod27` candidate spec을 GitOps `e7c32930...`의 정확히 한 커밋 자식으로 만들며, candidate 파일 하나만 변경한다. fixture를 실제 후보로 복사하지 않는다.
6. candidate를 로컬 검증하고 push한 뒤 GitOps candidate workflow를 dispatch한다.
7. candidate SHA가 생긴 뒤 Home dist, Frontend ET13 release-ready evidence, manual AT evidence 등 post-candidate producer를 fresh dispatch하고 보호 환경을 원자 승인한다.
8. 모든 evidence를 검증해 sealed manifest를 만든 뒤 staging/production promotion을 진행한다. 향후 protected `main` 병합 전에는 반드시 새 사용자 승인을 받는다.

## 재개 시 주의사항

- release ID `ms-20260909-prod27`은 원격 refs/candidate/release artifact와 충돌하지 않는 것으로 확인했다.
- 현재 production web rollback lineage는 sealed `ms-20260830-prod26r9`이다. `unreleased`로 되돌리거나 추측해 채우지 않는다.
- 현재 GitOps main web identity:
  - release ID `ms-20260830-prod26r9`
  - candidate SHA-256 `f86f010532dff3d231528f5ba4bc9b128704b9582393415b520ab981f7ad9ca5`
  - mission-on digest `sha256:278b1a862c5a9750f67cb1089f70ddba97fd1260af8e3ef3c95d0c1a3f16cc21`
- 보호 환경 승인은 동일 GitHub 계정의 repository atomic script만 사용한다. 매번 정확한 pending environment와 `restored_prevent_self_review=true`를 확인한다.
- GitOps protected main에 대한 추가 병합은 이 핸드오프 문서 push와 별개이며 새 승인 없이는 실행하지 않는다.
- Home 작업에 `cloudflare` 스킬을 적용했으며, 실제 Pages mutation 전 단계에서 중단했다.
