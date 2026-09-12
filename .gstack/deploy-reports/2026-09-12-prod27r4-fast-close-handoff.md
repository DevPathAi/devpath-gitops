# Mission Spine prod27r4 fast-close handoff

- 작성 시각: `2026-09-12T22:29:35.3898383+09:00`
- 릴리스 ID: `ms-20260909-prod27r4`
- 상태: `DONE_WITH_CONCERNS`
- 브랜치: `docs/prod27r4-post-landing-handoff-20260912`
- 기준 문서 커밋: `5382351a16db0e71640ca924f072414a5c53f9e0`
- 배포 상태: production, staging, Cloudflare Landing 모두 완료
- 미완료 큰 작업: 구형 classic GitHub PAT의 정확한 식별과 폐기

## 이번 세션 종료 결과

prod27r4 배포 자체는 완료 상태를 유지한다. promotion run `34686696557`과 Landing run `34687746592`는 성공이며, protected main은 `6c16aadea28a0818854ac761d11c019415572096`이다. Landing은 Cloudflare deployment `4c86f082-834d-4154-84e0-b0016725c97a`를 `mode=reuse`로 채택했고 중복 배포하지 않았다.

기존 post-Landing 문서가 미완료로 적었던 Temp 정리는 이번 세션에서 끝냈다. 아래 두 작업 디렉터리와 브라우저 연결 로그 네 파일은 정확한 절대 경로를 검증한 뒤 삭제했고, 재검사에서 모두 `Test-Path=False`였다.

- `C:\Users\deepe\AppData\Local\Temp\prod27r4-wrangler-bdd86c057ceb41bca7778dd53d6d4baa`
- `C:\Users\deepe\AppData\Local\Temp\prod27r4-landing-evidence-7451d0cb647b4664b843457b2e166269`
- `C:\Users\deepe\AppData\Local\Temp\prod27r4-gstack-connect-5a05691c2a234cf2a914dd04503ddc68.out.log`
- `C:\Users\deepe\AppData\Local\Temp\prod27r4-gstack-connect-5a05691c2a234cf2a914dd04503ddc68.err.log`
- `C:\Users\deepe\AppData\Local\Temp\prod27r4-gstack-connect-fb1c922b2e094c0291689f196f19abc2.out.log`
- `C:\Users\deepe\AppData\Local\Temp\prod27r4-gstack-connect-fb1c922b2e094c0291689f196f19abc2.err.log`

브라우저 자동화가 추가했던 `.gitignore`의 `.gstack/` 항목은 이 레포의 추적 대상인 `.gstack/deploy-reports/`까지 가리므로 제거했다. 이는 브라우저 실행 중 생긴 로컬 부작용의 원복이며 다른 변경은 포함하지 않는다. GStack 전용 Chromium 프로세스는 종료했고 재검사에서 남은 프로세스 수는 0이었다.

## 완료 증거 요약

- promotion: run `34686696557`, attempt `1`, conclusion `success`
- production canary: 900초 성공, artifact `10295454490`
- staging: mission-ON 멱등 재기준화 성공
- Landing: run `34687746592`, attempt `1`, conclusion `success`
- Landing evidence artifact: `10296118125`
- `evidence.json` SHA-256: `6F601AD4D248D2E2F295A0BA0F04CF731B04369CCE4DC8519F5B9E22585D6428`
- 임시 GitHub environment `CLOUDFLARE_API_TOKEN`: 삭제 확인
- release environment 5개: main-only, reviewer `VelkaressiaBlutkrone`, self-review 방지, admin bypass 비활성

상세 증거는 같은 디렉터리의 `2026-09-12-prod27r4-post-landing-handoff.md`에 있다.

## 다음 세션의 단일 작업

구형 classic `ghp_` PAT는 사용 경로에서 제거됐지만 GitHub 측 폐기 증거가 없다. 이번 세션에서는 `https://github.com/settings/tokens` 로그인 화면까지 실제로 열었으나 인증 정보는 입력하지 않았고 로그인 완료 전 종료했다. 토큰 이름은 아직 확인되지 않았으므로 추측해 폐기하면 안 된다.

다음 세션은 아래 순서만 수행한다.

1. GStack Browser 또는 연결된 Chrome에서 `https://github.com/settings/tokens`를 연다.
2. 사용자가 로그인, 비밀번호, passkey, 2FA를 직접 완료한다. 에이전트는 인증을 자동화하지 않는다.
3. classic token 목록에서 이름, 생성일, 만료일, last-used 메타데이터만 읽어 exact legacy token을 특정한다. 토큰 값은 읽거나 복사하거나 기록하지 않는다.
4. 대상이 하나로 특정되지 않으면 후보만 보고하고 중단한다.
5. exact token의 revoke/delete 최종 클릭 직전에 토큰 이름을 제시하고 `REVOKE <token-name>` 형식의 명시적 확인을 받는다.
6. 확인 뒤 exact token 하나만 폐기하고 목록 새로고침으로 부재를 증명한다.
7. 새 append-only 완료 체크포인트를 저장한다.

Windows의 GStack 패키징에서는 다음 실행 파일과 서버를 사용해야 한다.

```powershell
$env:BROWSE_SERVER_SCRIPT='C:\Users\deepe\.codex\skills\gstack\browse\dist\server-node.mjs'
$env:GSTACK_CHROMIUM_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'
& 'C:\Users\deepe\.codex\skills\gstack\browse\dist\browse.exe' connect
```

`browse.exe`가 기본 `server.ts`를 찾도록 두면 실패한다. UI 도구에 브라우저 surface가 있으면 그 경로를 먼저 사용하고, GStack fallback은 위 환경 변수를 유지한다.

## 10분 종료 규칙

앞으로 세션 종료 요청을 받으면 문서 정리, append-only 체크포인트, 의도한 문서만 커밋, 푸시, 복원 명령 제시를 10분 안에 끝낸다. 인증, 승인 대기, 장시간 배포, 불가역 보안 작업처럼 10분을 넘길 가능성이 있는 일은 상태와 정확한 재개 절차만 남기고 다음 세션의 첫 작업으로 이관한다.
