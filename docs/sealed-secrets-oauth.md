# platform OAuth 시크릿 — SealedSecret 운영 절차

platform-svc의 OAuth 클라이언트 자격증명(GitHub·Google)을 **SealedSecret**(암호화된 Secret)으로 관리한다. 평문 secret은 절대 커밋하지 않는다(CLAUDE.md).

## 구조

- deployment(`apps/devpath-platform-svc/base/deployment.yaml`)는 Secret `platform-oauth`를 `secretKeyRef`(`optional: true`)로 참조한다. Secret 미설정 시에도 pod는 기동하고 앱은 `application.yml`의 dummy 기본값으로 뜬다(OAuth 비활성).
- **Secret 이름**: `platform-oauth` (namespace `devpath`)
- **키**: `github-client-id`·`github-client-secret`·`google-client-id`·`google-client-secret`
- **소비 env**: `GITHUB_CLIENT_ID`·`GITHUB_CLIENT_SECRET`·`GOOGLE_CLIENT_ID`·`GOOGLE_CLIENT_SECRET` (platform `application.yml`의 `${...}` 매핑)

## 사전 준비 (클러스터당 1회)

### 1. SealedSecret 컨트롤러 설치 (cluster-wide)

AppProject `devpath`는 namespace `devpath`로 제한되어 cluster 리소스를 배포할 수 없다. 컨트롤러는 별도로 설치한다:

```bash
helm repo add sealed-secrets https://bitnami-labs.github.io/sealed-secrets
helm install sealed-secrets sealed-secrets/sealed-secrets -n kube-system
```

### 2. kubeseal CLI

컨트롤러와 호환되는 버전을 설치한다(예: `brew install kubeseal` / choco / GitHub 릴리스 바이너리).

## Google 자격증명 발급

Google Cloud Console → **API 및 서비스 → 사용자 인증 정보 → OAuth 2.0 클라이언트 ID(웹 애플리케이션)**:

- **승인된 리디렉션 URI**: `https://<platform-도메인>/login/oauth2/code/google`
- 발급된 **클라이언트 ID / 보안 비밀**을 확보

(GitHub은 GitHub → Settings → Developer settings → OAuth Apps에서 동일하게 발급, 콜백 `.../login/oauth2/code/github`.)

## SealedSecret 생성·커밋

```bash
# 1) 평문 Secret을 dry-run으로 생성 (파일을 디스크에 남기지 않도록 즉시 봉인)
kubectl create secret generic platform-oauth -n devpath \
  --from-literal=github-client-id=<GH_ID> \
  --from-literal=github-client-secret=<GH_SECRET> \
  --from-literal=google-client-id=<GOOGLE_ID> \
  --from-literal=google-client-secret=<GOOGLE_SECRET> \
  --dry-run=client -o yaml > /tmp/platform-oauth.yaml

# 2) kubeseal로 암호화 (컨트롤러 공개키 사용)
kubeseal --controller-namespace kube-system --controller-name sealed-secrets \
  -f /tmp/platform-oauth.yaml -o yaml \
  > apps/devpath-platform-svc/base/sealedsecret-oauth.yaml
rm -f /tmp/platform-oauth.yaml   # 평문 즉시 삭제

# 3) kustomization에 SealedSecret 리소스 추가
#    apps/devpath-platform-svc/base/kustomization.yaml 의 resources: 에
#    - sealedsecret-oauth.yaml  한 줄 추가
```

## 적용

- 위 산출물(`sealedsecret-oauth.yaml` + kustomization 수정)을 커밋 → `develop` → `main` PR. **배포는 `main` 기준 ApplicationSet**이 수행.
- ArgoCD가 SealedSecret을 적용 → 컨트롤러가 복호화해 Secret `platform-oauth` 생성 → pod가 env로 주입.
- 렌더 확인: `kubectl kustomize apps/devpath-platform-svc/base`

## 주의

- **평문 client-secret 커밋 금지.** SealedSecret(암호문)만 커밋한다.
- kubeseal은 **해당 클러스터 컨트롤러 공개키**로 암호화하므로, 다른 클러스터에서는 복호화되지 않는다(클러스터별로 재봉인 필요).
- 리디렉션 URI가 실제 배포 도메인과 정확히 일치해야 OAuth 콜백이 성공한다.
