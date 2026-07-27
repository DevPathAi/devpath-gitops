# 시크릿 파일 준비 가이드 — WS-D 실배포 6종

> 실배포(leva.ai.kr)에 필요한 시크릿 6종을 **로컬 JSON 파일 1개**로 준비하는 방법.
> 이 파일은 봉인(SealedSecret) 입력으로만 쓰이고 **봉인 직후 삭제**한다. 절대 커밋하지 않는다.
> 봉인 절차 자체는 [sealed-secrets-oauth.md](./sealed-secrets-oauth.md), 클러스터 전반은 [runbook-k3s-bootstrap.md](./runbook-k3s-bootstrap.md) 참조.

## 1. 파일 형식

로컬 임의 경로(예: `C:\Users\<user>\devpath-secrets.json`)에 저장:

```json
{
  "github_client_id": "Ov23li...",
  "github_client_secret": "...",
  "google_client_id": "1234567890-abc...apps.googleusercontent.com",
  "google_client_secret": "GOCSPX-...",
  "claude_api_key": "sk-ant-api03-...",
  "ghcr_pat": "ghp_..."
}
```

- 저장 위치는 **레포 밖**(홈 디렉토리 등). 클라우드 동기화 폴더는 피한다.
- 사용(봉인) 완료 후 파일을 삭제한다.

## 2. 값별 획득 방법

### 2-1. GitHub OAuth (`github_client_id` / `github_client_secret`)

운영용 **신규 OAuth App**을 만든다 (GitHub OAuth App은 Authorization callback URL이 1개뿐이라 로컬 개발용과 분리).

1. github.com → 우상단 프로필 → **Settings** → 좌측 맨 아래 **Developer settings**
2. **OAuth Apps** → **New OAuth App**
3. 입력값:
   - Application name: `DevPath (beta)`
   - Homepage URL: `https://app.leva.ai.kr`
   - **Authorization callback URL: `https://api.leva.ai.kr/login/oauth2/code/github`**
4. **Register application** → 표시되는 **Client ID** 복사
5. **Generate a new client secret** → 생성된 secret 즉시 복사(다시 볼 수 없음)

### 2-2. Google OAuth (`google_client_id` / `google_client_secret`)

기존 OAuth 2.0 클라이언트(웹 애플리케이션)에 리디렉션 URI만 추가하면 된다(여러 개 등록 가능).

1. console.cloud.google.com → 해당 프로젝트 → **API 및 서비스 → 사용자 인증 정보**
2. 기존 **OAuth 2.0 클라이언트 ID(웹 애플리케이션)** 클릭 (없으면 신규 생성)
3. **승인된 리디렉션 URI**에 추가: `https://api.leva.ai.kr/login/oauth2/code/google` → 저장
4. 같은 화면의 **클라이언트 ID**·**클라이언트 보안 비밀번호** 복사
5. 주의: **OAuth 동의 화면**이 "테스트" 게시 상태면 테스트 사용자 목록에 등록된 계정만 로그인 가능 — 베타 참여자 이메일을 테스트 사용자에 추가하거나 "프로덕션" 게시로 전환

### 2-3. Claude API 키 (`claude_api_key`)

1. console.anthropic.com → **API Keys** → **Create Key**
2. 이름 예: `devpath-beta` → 생성된 `sk-ant-...` 즉시 복사(다시 볼 수 없음)
3. ai-svc의 리뷰·멘토·시드·리텐션 실호출에 사용된다(과금 발생 — Console에서 사용량·한도 확인 가능)

### 2-4. ghcr PAT (`ghcr_pat`)

ghcr.io의 private 이미지(devpath-* 10종)를 클러스터가 pull할 자격. **read:packages 단일 스코프**로 최소화한다.

1. github.com → **Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. **Generate new token (classic)**
3. Note 예: `devpath-k3s-pull` / Expiration: 90일 권장(만료 시 재발급·재봉인)
4. 스코프: **`read:packages`만 체크** (다른 스코프 불필요)
5. Generate → `ghp_...` 복사

## 3. 검증 체크리스트 (전달 전 셀프 체크)

| 필드 | 형식 확인 |
|---|---|
| github_client_id | `Ov23li` 또는 20자 내외 영숫자 |
| github_client_secret | 40자 내외 hex |
| google_client_id | `...apps.googleusercontent.com`으로 끝남 |
| google_client_secret | `GOCSPX-` 시작(신형 기준) |
| claude_api_key | `sk-ant-` 시작 |
| ghcr_pat | `ghp_` 시작, read:packages 스코프 |

## 4. 전달 후 처리 (참고 — 봉인 매핑)

| 파일 필드 | Secret(devpath ns) | 키 | 소비처 |
|---|---|---|---|
| github/google 4종 | `platform-oauth` | `github-client-id`·`github-client-secret`·`google-client-id`·`google-client-secret` | platform-svc OAuth 로그인 |
| claude_api_key | `ai-claude` | `anthropic-api-key` | ai-svc `ANTHROPIC_API_KEY`(SDK) |
| ghcr_pat | `ghcr-pull` | `.dockerconfigjson` (docker-registry 타입) | 전 deployment `imagePullSecrets` |

- 봉인은 EC2에서 kubeseal(컨트롤러 `sealed-secrets-controller`, kube-system)로 수행 → `apps/*/base/sealedsecret-*.yaml` 커밋(develop→main 릴리스) → ArgoCD 적용.
- 봉인 완료 즉시 로컬 JSON·EC2 임시 파일을 삭제한다.
- OAuth 콜백 URI는 위 2-1·2-2에 이미 포함 — 별도 등록 단계 없음.
