# Runbook — AWS k3s 베타 클러스터 부트스트랩 (2026-07-27 실측)

> WS-D 실배포 세션의 **실제 실행 기록**. 재구축·복구 시 이 순서를 따른다.
> 계획 SSoT: `docs/superpowers/plans/2026-07-05-aws-k3s-deployment.md` + documents `docs/superpowers/plans/2026-07-27-ws-d-deploy-session.md`.
> 비밀값은 기록하지 않는다(SealedSecret·로컬 파일 경유).

## 확정값 (2026-07-27 프로비저닝)

| 항목 | 값 |
|---|---|
| 리전 | ap-northeast-2 (기본 VPC vpc-0d82a0f66bb7b4bcd) |
| EC2 | `devpath-k3s` = **i-09e252854566cc123**, t3.xlarge, Ubuntu 22.04(ami-0195f90f654bc4d8e), EBS gp3 50GB |
| EIP | **13.124.153.105** (eipalloc-0a3fcbe8cc095274c) |
| RDS | `devpath-pg` = **devpath-pg.c7emuq20mhyy.ap-northeast-2.rds.amazonaws.com**, PostgreSQL 17, db.t4g.micro 20GB, 백업 7일, 비공개 |
| SG | `devpath-k3s-sg`(sg-0ad7dfa8afe5d1eea): 22·6443←관리IP, 80·443←any / `devpath-rds-sg`(sg-05564b46395296ffd): 5432←k3s-sg |
| 키페어 | `devpath-k3s-key` (ed25519, 로컬 `~/.ssh/devpath-k3s-key.pem`) |
| DNS | `api`·`app`·`admin`.leva.ai.kr → EIP (가비아 NS) |
| k3s | v1.36.2+k3s1 (Traefik·local-path 내장) |
| Kafka | Strimzi **1.1.0** (KRaft 전용, API **kafka.strimzi.io/v1**) — `kafka/kafka-cluster.yaml`, 부트스트랩 `devpath-kafka-bootstrap.kafka.svc:9092` |
| Redis | `apps/devpath-redis`(ApplicationSet 자동 발견) — `redis.devpath.svc:6379` |
| SealedSecrets | 컨트롤러 v0.27.3 (**release manifest로 설치** — helm repo URL 404), 이름 `sealed-secrets-controller`(kube-system) |
| Secrets(devpath ns) | `platform-db`(db-url/db-user/db-password) · `devpath-jwt`(jwt-secret) · `platform-oauth`(4키) · `ai-claude`(anthropic-api-key) · `ghcr-pull`(dockerconfigjson) |

## 부트스트랩 순서 (요약)

1. **AWS**: SG 2종·키페어 → EC2(t3.xlarge)+EIP → RDS(pgvector: EC2에서 `psql ... -c 'CREATE EXTENSION IF NOT EXISTS vector;'`).
2. **k3s**: `curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--write-kubeconfig-mode 644" sh -`. kubeconfig는 `/etc/rancher/k3s/k3s.yaml`(로컬 백업 시 server를 EIP로 교체). EC2 위 `kubectl`은 k3s 내장 — 다른 도구(kubeseal 등)는 `export KUBECONFIG=/etc/rancher/k3s/k3s.yaml` 필요.
3. **ArgoCD**: `kubectl create ns argocd` 후 install.yaml을 **`--server-side --force-conflicts`로 적용**(ApplicationSet CRD가 client-side annotation 262KB 제한 초과). gitops는 public이라 repo 자격 불요. `argocd/project.yaml`·`applicationset.yaml` 적용 → `apps/*` 자동 발견(revision main).
4. **SealedSecrets**: `kubectl apply -f .../v0.27.3/controller.yaml`(helm repo 404 주의). 봉인: `kubectl create secret ... --dry-run=client -o yaml | kubeseal --controller-namespace kube-system --controller-name sealed-secrets-controller -o yaml` → `apps/<svc>/base/sealedsecret-*.yaml` 커밋.
5. **Kafka**: `kubectl create ns kafka` + `kubectl create -f 'https://strimzi.io/install/latest?namespace=kafka' -n kafka` → `kafka/kafka-cluster.yaml` 적용(**v1 API·KRaft·KafkaNodePool** — v1beta2는 거부됨) → `kubectl -n kafka wait kafka/devpath --for=condition=Ready`.
6. **Redis·env 배선·ingress**: gitops 변경은 항상 `작업 브랜치 → develop PR → develop→main 릴리스 PR`(main 직접 push 금지, bot 커밋만 예외). 릴리스 전 `git merge origin/main` 백머지로 bot SHA 충돌 예방(태그는 main 쪽 채택).
7. **cert-manager**: release yaml 적용 → `infra/cert-manager/cluster-issuer.yaml`(cluster 리소스, EC2 직접 적용).
8. **ghcr pull 자격**: 패키지가 private이므로 `ghcr-pull` docker-registry Secret(read:packages PAT) + deployment `imagePullSecrets` 필요.

## 마이그레이션 (RDS)

- Job = `apps/devpath-migration`(구 `apps/_migration` — **언더스코어 이름은 Application 생성 불가(RFC1123)로 개명**, shared ci.yml deploy 잡 경로도 함께 수정).
- 이미지 `ghcr.io/devpathai/devpath-migration`(flyway 11 + SQL 내장, shared CI가 main 릴리스마다 발행·태그 커밋). 자격은 `platform-db` Secret의 `FLYWAY_URL/USER/PASSWORD`.
- **Job은 immutable** — 태그 교체 후 sync 실패 시: `kubectl -n devpath delete job devpath-flyway-migrate` → ArgoCD 재sync(재생성).
- 성공 판정: `kubectl -n devpath logs job/devpath-flyway-migrate` 에 "Successfully applied N migrations".

## 트러블슈팅 (이번 세션 실측)

| 증상 | 원인 | 해법 |
|---|---|---|
| ArgoCD CRD 적용 실패(annotation too long) | client-side apply 262KB 제한 | `kubectl apply --server-side --force-conflicts` |
| sealed-secrets helm repo 404 | 차트 저장소 이동/폐기 | release `controller.yaml` 직접 적용, 컨트롤러명 `sealed-secrets-controller` |
| Kafka CR `no matches for kind` | Strimzi 1.x는 `kafka.strimzi.io/v1`만 서빙 | apiVersion을 v1으로, KRaft(KafkaNodePool) 구성 |
| ApplicationSet reconcile 에러 반복 | `apps/_migration` → 이름 `_migration` RFC1123 위반 | 디렉토리를 `apps/devpath-migration`으로 개명 |
| 전 pod ImagePullBackOff | ghcr 패키지 private + pull 자격 없음 | `ghcr-pull` Secret + default SA `imagePullSecrets` 패치(+기존 pod 재생성 필요) |
| frontend admin-image "Cache export is not supported" | buildx 기본 docker driver가 gha 캐시 미지원 | ci.yml에 `docker/setup-buildx-action@v3` 명시 |
| svc CI pgvector pull 타임아웃(플레이크) | Docker Hub 레이트/네트워크 | `gh run rerun <id> --failed` |
| ArgoCD가 새 커밋을 늦게 봄 | 폴링 주기(~3분) | `kubectl -n argocd annotate application <app> argocd.argoproj.io/refresh=hard --overwrite` |
| kubeseal "no configuration provided" | KUBECONFIG 미설정(k3s 전용 경로) | `export KUBECONFIG=/etc/rancher/k3s/k3s.yaml` |
| migration Job `CreateContainerConfigError` (runAsNonRoot vs root image) | flyway 공식 이미지 기본 유저=root | job securityContext에 `runAsUser: 1000` 명시 |
| Spring `${REDIS_PORT:6379}` int 바인딩 붕괴(`tcp://10.x:6379`) | `redis` Service로 K8s legacy service-link env 자동 주입 | 전 백엔드 deployment `enableServiceLinks: false` |
| 프로브 401 → liveness 재시작 루프 | `/actuator/health`만 permitAll(프로브는 하위 경로) | 7 svc SecurityConfig `/actuator/health/**` 추가(notif #12 패턴) |
| ai-svc 기동 실패 "required a single bean, but 4 were found" | provider=claude 4종 동시 활성 + review 소비자만 @Qualifier 누락 | `ClaudeAiReviewClient`에 `@Qualifier("anthropicClient")` |
| 롤링 중 CrashLoop "remaining connection slots are reserved" | RDS db.t4g.micro max_connections(~85) vs 7 svc×풀10 | 전 DB svc env `SPRING_DATASOURCE_HIKARI_MAXIMUM_POOL_SIZE=5` + 순차 재기동 |
| OAuth redirect_uri에 `%0D`(CR) | CRLF 시크릿 파일 파싱 잔재 | 봉인 파이프라인에 `tr -d '\r\n'` + 재봉인 |
| OAuth redirect_uri가 내부 svc DNS/http | ①platform 프록시 헤더 미반영 ②gateway Host 재작성 ③SCG 신버전 trusted-proxies 미설정 시 X-Forwarded 제거 | platform `SERVER_FORWARD_HEADERS_STRATEGY=framework` + gateway `PreserveHostHeader`·`TRUSTED_PROXIES=10\..*` + (이중보장) `SPRING_SECURITY_OAUTH2_CLIENT_REGISTRATION_{GITHUB,GOOGLE}_REDIRECTURI` 절대값 |
| 브라우저 CORS 차단 (`No Access-Control-Allow-Origin`) | gateway `CORS_ALLOWED_ORIGINS` 기본값(localhost)뿐 | env에 `https://app.leva.ai.kr,https://admin.leva.ai.kr` 주입 |
| 로그인 후 `/beta-pending`+401 두 건 | 베타 미승인 분기는 토큰·쿠키 없이 리다이렉트(설계) | `beta_allowlist`에 email INSERT(또는 admin UI 승인) 후 **재로그인** |

## E2E 준비 절차 (실측 기록)

- 최초 ADMIN: 첫 로그인으로 users 행 생성 후 `UPDATE users SET role='ADMIN' WHERE id=<id>;` (role CHECK: LEARNER/ADMIN)
- 베타 승인 지름길: `INSERT INTO beta_allowlist(email, note, added_by) VALUES ('<email>','...','system');` — **재로그인해야 실 토큰 발급**
- 주의: 실제 가입 이메일은 GitHub 계정 이메일(이번 실측: `deepestdark@outlook.kr` — gmail 아님). `BETA_ADMIN_EMAILS`도 이 값과 일치시켜야 함.
- psql 접속: EC2에서 `PGPASSWORD=$(cat ~/.secrets/rds-pw.txt) psql "host=<RDS> user=devpath dbname=devpath"`

## 🔴 미해결 (OPEN) — 다음 세션 착수 지점

**증상**: 베타 allowlist 등록 후에도 웹 로그인 → **가입 동의 화면에서 진행 불가**. 콘솔에 `/dashboard/me` 401·`/auth/refresh` 401 (CORS는 해소된 상태 — 401이 서버 응답으로 도달).

**검증된 사실** (재조사 불필요):
- OAuth authorize 요청 정상: `redirect_uri=https://api.leva.ai.kr/login/oauth2/code/github`, client_id CR 없음
- gateway CORS: `allowCredentials(true)` + 운영 origin 허용, preflight 200
- 웹·admin dio: `options.extra['withCredentials'] = true` 설정돼 있음 (api_providers.dart)
- RefreshCookies: `refresh_token` HttpOnly, `Domain=.leva.ai.kr`(COOKIE_DOMAIN), `Secure`(COOKIE_SECURE=true), `SameSite=Lax` — env 반영 확인
- allowlist 등록·role=ADMIN 완료(id=1, deepestdark@outlook.kr). Redis beta-status 캐시는 정리했음(부작용: 열려 있던 pending 탭의 statusToken이 무효화 → 그 탭은 승인 감지 불가, **새 로그인 필요**)

**미확인 — 다음 진단 순서**:
1. 사용자가 **완전한 재로그인**(pending 탭 아님, 새로 GitHub 로그인)을 거쳤는지 — 승인 후 재로그인해야 SuccessHandler 113행(웹 분기)에서 refresh 쿠키가 발급됨
2. 재로그인 시 브라우저 DevTools Network: `/auth/callback` 복귀 응답(302)에 `Set-Cookie: refresh_token=...; Domain=.leva.ai.kr; Secure; SameSite=Lax` 존재 확인
3. 이어지는 `/auth/refresh` 요청 헤더에 `Cookie: refresh_token=...` 포함 확인 — 미포함이면 쿠키 저장/전송 문제(가능성: `ai.kr` Public Suffix 판정으로 `Domain=.leva.ai.kr` 거부 여부 — DevTools Application→Cookies에서 실측), 포함인데 401이면 platform refreshStore(Redis) 검증 로그 확인
4. platform 로그 레벨 상향(`LOGGING_LEVEL_AI_DEVPATH=DEBUG` env) 후 로그인 1회 재현 — SuccessHandler 어느 분기로 갔는지 확정
5. 동의 화면 제출 API 경로·요구 토큰 확인 (frontend consent feature ↔ platform /consents)

**보조 단서**: OAuth2LoginSuccessHandler 분기 — 85행(베타 미승인, 쿠키 없음)·113행(웹 정상, refresh 쿠키+`/auth/callback`). 401 두 건은 85행 분기의 예상 로그. allowlist 등록 "후"에도 같은 401이면 admit()이 여전히 false거나(캐시/조회 문제) 재로그인 미수행.

## 정리(리소스 폐기) — 비용 중단 시

```bash
aws ec2 terminate-instances --region ap-northeast-2 --instance-ids i-09e252854566cc123
aws ec2 release-address --region ap-northeast-2 --allocation-id eipalloc-0a3fcbe8cc095274c
aws rds delete-db-instance --region ap-northeast-2 --db-instance-identifier devpath-pg --skip-final-snapshot
# SG·키페어는 인스턴스 종료 후: delete-security-group / delete-key-pair
```
