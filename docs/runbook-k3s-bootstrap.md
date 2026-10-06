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

## GitHub Actions → k3s API 접근 경계 (2026-08-30 실측)

GitHub-hosted runner의 출발지 IP는 실행마다 달라지므로 고정 관리 IP 규칙으로는 보호된 릴리스 잡이
`13.124.153.105:6443`에 도달할 수 없다. mission-spine의 원격 Kubernetes 잡은 장기 허용 CIDR이나
정적 AWS 키 대신 다음 경계를 사용한다.

- GitHub OIDC 역할: `arn:aws:iam::963773969059:role/devpath-github-actions-k3s-api`
- 신뢰 subject: `DevPathAi/devpath-gitops`의 `mission-spine-staging`,
  `mission-spine-production-off`, `mission-spine-production-on`,
  `mission-spine-production-rollback` 환경만 허용한다.
- 권한: `sg-0ad7dfa8afe5d1eea`의 ingress 허용·회수와 생성된 SG rule 태깅·조회만 허용한다.
- 실행: `scripts/release/manage_kubernetes_api_ingress.py open`이 프록시를 우회해 확인한 현재 runner의
  canonical global IPv4 하나만 `tcp/6443` `/32`로 추가한다. kubeconfig 정리 뒤 `close`가 태그·SG·포트·
  CIDR을 다시 검증하고 해당 rule ID만 회수한다.
- 각 규칙에는 `ManagedBy=devpath-mission-spine-github-actions`, GitHub run/job, 만료 epoch가 붙는다.
  workflow와 AWS credential action은 보호된 `main`의 고정 SHA에서만 실행한다.

강제 취소나 runner 장애 뒤 남은 관리 규칙 감사:

```bash
aws ec2 describe-security-group-rules --region ap-northeast-2 \
  --filters Name=group-id,Values=sg-0ad7dfa8afe5d1eea \
            Name=tag:ManagedBy,Values=devpath-mission-spine-github-actions \
  --query 'SecurityGroupRules[].{Id:SecurityGroupRuleId,Cidr:CidrIpv4,Description:Description,Tags:Tags}'
```

정상 완료 뒤 결과는 빈 배열이어야 한다. 잔여 규칙은 위 출력에서 정확한 rule ID와 태그·범위를 먼저
확인한 후 `aws ec2 revoke-security-group-ingress --region ap-northeast-2
--group-id sg-0ad7dfa8afe5d1eea --security-group-rule-ids <sgr-id>`로 회수한다.

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
| 로그인 후 동의 화면 401 (`/auth/refresh`·`/dashboard/me`) | platform 비원자 단일-사용 회전 × 웹 동시 refresh → 뒤따른 요청 401 → 인터셉터 store.clear()로 세션 파괴 | platform 회전 유예창 30s(`devpath.auth.refresh-rotate-grace`, PR #40·릴리스 #41) |
| 무인증(무쿠키) 부팅 시 `/#/login` 미도달 무한 스피너 | 앱이 refresh/retry를 같은 dio로 재진입 → QueuedInterceptor 에러 큐 순환 대기(교착) → AuthLoading 영구 고착 | frontend authFlow 전용 클라이언트 분리(무-AuthInterceptor, PR #82·릴리스 #83) |
| 동의 화면에서 제출 버튼이 회색으로만 남아 진행 불가 | 출생 연도 미등록(IME 조합·전각 숫자를 digitsOnly가 조용히 삼킴 포함) + 조용한-비활성 버튼(안내 부재) | 버튼 상시 활성 + 제출 시 검증·에러 안내 + 전각 정규화(frontend PR #84·릴리스 #85) |

## E2E 준비 절차 (실측 기록)

- 최초 ADMIN: 첫 로그인으로 users 행 생성 후 `UPDATE users SET role='ADMIN' WHERE id=<id>;` (role CHECK: LEARNER/ADMIN)
- 베타 승인 지름길: `INSERT INTO beta_allowlist(email, note, added_by) VALUES ('<email>','...','system');` — **재로그인해야 실 토큰 발급**
- 주의: 실제 가입 이메일은 GitHub 계정 이메일(이번 실측: `deepestdark@outlook.kr` — gmail 아님). `BETA_ADMIN_EMAILS`도 이 값과 일치시켜야 함.
- psql 접속: EC2에서 `PGPASSWORD=$(cat ~/.secrets/rds-pw.txt) psql "host=<RDS> user=devpath dbname=devpath"`

## ✅ 해소 (2026-07-27 저녁) — 로그인 후 동의 화면 401 (구 🔴 OPEN)

**원인은 두 겹**이었다 (증상: 동의 화면 진행 불가 + `/auth/refresh`·`/dashboard/me` 401):

1. **서버 — refresh 회전 경쟁**: `RefreshTokenStore.rotate()`가 validate→DEL→issue **비원자
   단일-사용**이라, 웹의 동시 refresh(콜백 이중 부트스트랩·401 인터셉터 재시도·멀티탭)에서
   뒤따른 요청이 반드시 401 → `AuthInterceptor` 실패 경로의 `store.clear()`가 세션 파괴.
   운영 실측: 동일 토큰 동시 2회 `{200,401}` 및 이중 발급 `{200,200}` 재현(8/8 라운드 비원자성).
   → **수정**: 회전 유예창 30s(`devpath.auth.refresh-rotate-grace`, 0=비활성 — platform PR #40·
   릴리스 #41). 수정 후 실측: 순차 스테일 재사용 200→200, 동시 8라운드 401 제로.
2. **클라 — AuthInterceptor 재진입 교착**: 앱 배선(web·admin·mobile)이 refresh/retry를 **같은
   dio**로 호출 → refresh 자신이 401이면 QueuedInterceptor 에러 큐가 자기 자신을 대기(교착) →
   무쿠키/무효쿠키 부팅에서 bootstrapSession 영구 미완결 → AuthLoading 고착 → **`/#/login`
   미도달 무한 스피너**(로그인 시도 자체가 불가 — 서버에 OAuth 흔적이 전무했던 이유).
   → **수정**: 앱 3종에 authFlow 전용 클라이언트(무-AuthInterceptor) 분리 + dp_core 회전 가드
   보강(frontend PR #82·릴리스 #83). 수정 후 실측(헤드리스, 신규 번들): 무쿠키 부팅 →
   `/#/login` 정상 도달, 유효 쿠키 부팅 → `/#/consent` 도달(refresh 200·dashboard 재시도 200).
   동의 제출 → `/#/diagnostic` 완주는 서버 수정 직후 실측(같은 서버·이전 번들, 콘솔 에러 0 —
   제출 경로는 클라 수정과 무관).
3. **클라 — 동의 화면 조용한-비활성 버튼**(위 두 수정 후 드러난 세 번째 결함): 출생 연도가
   등록되지 않으면(미입력 또는 IME 조합·전각 숫자를 `digitsOnly` 라이브 필터가 조용히 삼킴)
   제출 버튼이 **아무 안내 없이 회색**으로만 남아 사용자가 원인을 모른 채 갇힘(실사용 재현:
   체크 5종 완료 + 연도 빈 값 + 회색 버튼). 클린 브라우저 원시 입력(포인터+키)은 정상 등록 —
   환경(IME) 의존.
   → **수정**: 버튼 상시 활성 + 제출 시 검증(필수 미체크/연도 미입력을 명시적 에러 텍스트로
   안내, 연도 필드 포커스 이동), `digitsOnly` 제거 후 제출 시 파싱 검증, 전각 숫자 반각
   정규화(frontend PR #84·릴리스 #85). 수정 후 실측(헤드리스, fa5eb91 번들): 빈 연도 제출 →
   에러 문구 노출·필드 포커스, 입력 후 제출 → `POST /consents` 200 → `/#/diagnostic` 완주.

**기각된 가설(재조사 불필요)**: PSL 쿠키 거부(`ai.kr`은 PSL **단독 항목**이라 `leva.ai.kr`이
등록 가능 도메인 → `Domain=.leva.ai.kr` 유효. `*.ai.kr` 항목 없음)·CORS credentials·재로그인
미수행·SuccessHandler 85행 미승인 분기(id=1은 role=ADMIN이라 `admit()` 무조건 true).

**부수 사실**: users 2계정(id=1 outlook.kr=GitHub / id=2 gmail.com=Google — 이메일 상이로 계정
분리, 정상 동작)·루트 `leva.ai.kr`은 A레코드 없음(주소창 직접 진입 실패는 현재 정상 — WS-A에서
처리)·`BetaGate.admit()`의 ADMIN 조기 return은 status를 BETA_PENDING으로 방치(기능 영향 미확인).

**남은 후속(백로그)**: ① frontend refresh single-flight(부트스트랩 이중 호출·첫 로드 무토큰
선발사 401 콘솔 잔상 1건 정리) ② 유예창 밖 재사용 감지(reuse detection) ③ BetaGate ADMIN
status 승격 정리 ④ Redis 영속성(재시작 시 전 세션 로그아웃) 검토.

## 정리(리소스 폐기) — 비용 중단 시

```bash
aws ec2 terminate-instances --region ap-northeast-2 --instance-ids i-09e252854566cc123
aws ec2 release-address --region ap-northeast-2 --allocation-id eipalloc-0a3fcbe8cc095274c
aws rds delete-db-instance --region ap-northeast-2 --db-instance-identifier devpath-pg --skip-final-snapshot
# SG·키페어는 인스턴스 종료 후: delete-security-group / delete-key-pair
```

---

## GPU 노드 추가 (2026-08-17 실측)

학습경로 생성을 CPU 로는 돌릴 수 없어(4.2 t/s × 약 3000토큰 ≈ 12분) GPU 워커를 붙인다.

### 확정값

| 항목 | 값 |
|---|---|
| 인스턴스 | g6.xlarge (NVIDIA **L4 23034 MiB**), ap-northeast-2d — 기존 노드와 같은 AZ |
| AMI | `ami-09d3bdf0648512f52` = Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04). 드라이버 **595.91.07** 과 `nvidia-container-runtime` 이 이미 들어 있다 |
| 단가(서울) | 온디맨드 **$0.9896/h** · 스팟 **약 $0.2824/h**(2d 실측) |
| 쿼터 | 온디맨드 `L-DB2E81BA` = 4 vCPU 승인. **스팟은 `L-3819A6DF` 로 별개**이며 기본 0 |
| 루트 볼륨 | **120 GiB gp3** (`/dev/sda1`) — AMI 기본값 75 GiB 를 그대로 쓰지 않는다(아래 「루트 볼륨」, 2026-10-06 결정) |

### 절차

1. **SG**: 같은 SG(`sg-0ad7dfa8afe5d1eea`) 안에서만 통하도록 self-referencing 규칙 3개를 더한다 — `6443/tcp`(agent→server API), `8472/udp`(flannel VXLAN), `10250/tcp`(kubelet). 외부에는 아무것도 열지 않는다.
2. **인스턴스**: 위 AMI·타입으로 기존 서브넷(`subnet-00bb150fb3e236ecb`)에 띄운다. 키페어는 `devpath-k3s-key`.
   루트 볼륨을 명시한다 — 지정하지 않으면 AMI 기본값 75 GiB 로 뜬다.

   ```bash
   --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":120,"VolumeType":"gp3","DeleteOnTermination":true}}]'
   ```

   태그도 명시한다 — `role=k3s-agent-gpu` 가 없으면 「미복구 반복 통지」(아래)가 6시간마다 계속 온다.

   ```bash
   --tag-specifications 'ResourceType=instance,Tags=[{Key=role,Value=k3s-agent-gpu},{Key=Name,Value=devpath-k3s-gpu}]'
   ```

   기동 뒤 `df -h /` 가 **117G** 를 보여야 한다(2026-10-06 실측 — cloud-init `growpart`·`resizefs` 가 첫 부팅에 파일시스템을 늘린다).
3. **조인 토큰**: 서버의 `/var/lib/rancher/k3s/server/node-token`. **user-data 에 넣지 않는다** — 인스턴스 메타데이터는 노드 위 아무 프로세스나 읽을 수 있다. scp 로 옮기고 조인 후 지운다.
4. **k3s agent**: 서버와 **같은 버전으로 고정**한다.

   ```bash
   curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.36.2+k3s1 \
     K3S_URL=https://172.31.48.82:6443 K3S_TOKEN=<token> \
     sh -s - agent --node-label devpath.ai/gpu=true --node-taint devpath.ai/gpu=true:NoSchedule
   ```

   **테인트가 핵심이다.** 없으면 기존 서비스 파드가 재시작될 때 이 노드로 흘러들어가고, 스팟이 회수되면 관계없는 서비스까지 함께 내려간다.
5. **device plugin**: `kubectl apply -f infra/nvidia-device-plugin/daemonset.yaml`. 이 매니페스트에는 위 테인트에 대한 톨러레이션과 `nodeSelector` 가 들어 있다 — 없으면 플러그인 자신이 스케줄되지 못한다.
6. **검증**: `kubectl get node <gpu-node> -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'` 가 `1` 이어야 한다. 파드 안에서 `nvidia-smi` 도 확인한다.

### 실측 결과

- `nvidia` RuntimeClass 는 클러스터에 이미 있고, agent 설치 시 containerd 설정에 nvidia 런타임이 등록된다(`/var/lib/rancher/k3s/agent/etc/containerd/config.toml`).
- 테인트 격리 확인: GPU 노드에 뜬 파드는 `ollama-gpu` 와 device plugin **둘뿐**이었다.
- 생성 성능: **66 t/s(순간 97 t/s)** — 운영 CPU 4.2 t/s 대비 약 16배. 12주 한국어 경로 1건이 ai-svc 왕복 포함 **86초**(CPU 는 12분).

### 스팟 회수 후 복구 (2026-10-01 실측)

2026-09-08 에 GPU 스팟 노드(`ip-172-31-52-213`)가 회수됐고 **23일 동안 아무도 알지 못했다.**
`ollama-gpu` 서비스가 엔드포인트 0개인 채로 남아 학습경로 생성이 잠복 고장 상태였다
(그 사이 트래픽이 없어 사용자 영향은 없었다). 복구는 아래 순서여야 한다 —
**새 노드만 띄우면 파드는 여전히 Pending 이다.**

1. 선결 조건 재확인(2026-10-01 실측): 스팟 쿼터 `L-3819A6DF` = **4 vCPU 승인됨**(기본 0 에서 증설 완료) ·
   SG self-referencing 3규칙 유지 · AMI `ami-09d3bdf0648512f52` 유효 · 서브넷 퍼블릭(IGW).
   **스팟 시세는 2026-08-17 의 $0.2824/h 에서 $0.4587/h 로 올랐다**(온디맨드 $0.9896/h 대비 46%).
2. 인스턴스 기동(**루트 볼륨 120 GiB** — 위 「절차」 2 의 `--block-device-mappings`) → 「절차」 3·4 로 조인(토큰은 scp 후 삭제).
3. **죽은 노드 오브젝트를 지운다**: `kubectl delete node <old>`. 지우지 않으면 `dueProbes` 류 집계와
   스케줄러 메시지가 계속 그 노드를 센다.
4. **`Terminating` 으로 멈춘 옛 파드를 강제 삭제한다**: `kubectl delete pod -n devpath <old-pod>
   --force --grace-period=0`. 노드 오브젝트를 지워도 이 파드는 남고, 그게 PVC 의
   `kubernetes.io/pvc-protection` finalizer 를 붙들어 다음 단계가 막힌다.
5. ★**PVC 를 지운다**★ — `ollama-gpu-models` 의 PV 는 local-path 라
   `nodeAffinity: kubernetes.io/hostname In [<old-node>]` 로 **죽은 노드에 고정**돼 있다.
   그대로 두면 새 GPU 노드가 Ready 여도 파드는 영원히 Pending 이다. 모델 캐시는 노드와 함께
   이미 사라졌으므로 버려도 된다(reclaim policy = Delete). 지우면 ArgoCD 가 매니페스트로
   재생성하고 새 노드에 새 PV 가 붙는다.
6. 검증: `nvidia.com/gpu` allocatable = 1 · 파드 안 `nvidia-smi` · 엔드포인트 ready=true ·
   생성 속도. **2026-10-01 실측 58.8 tok/s**(CPU Ollama 4.9 tok/s 대비 12배).
7. **모델 2종이 다 올라왔는지 확인한다**(`ollama list`): `qwen2.5:3b`(학습경로 생성) ·
   `qwen2.5:7b`(review·community-seed·retention 의 Claude 폴백). postStart 가 둘 다 pull 하지만
   백그라운드라 Ready 직후에는 아직 없을 수 있다 — 7b 는 4.7GB 로 수 분 걸린다.
   7b 가 없으면 세 기능의 폴백이 「model not found」404 로 떨어진다.
   첫 호출은 콜드 로드를 떠안는다(**2026-10-02 실측 24.8초**, 타임아웃 60초 안). `OLLAMA_KEEP_ALIVE=24h`
   라 그 뒤로는 웜(retention 0.8초)이다.

**2026-10-06 두 번째 회수와 복구(실측)** — 2026-10-01 에 띄운 노드(`ip-172-31-52-85`)가 5일 만에 회수됐다.

| 시각(UTC) | 일 |
|---|---|
| 04:34 | Rebalance Recommendation(회수 아님) |
| 17:22 | Interruption Warning → 스팟 요청 `instance-terminated-no-capacity`. 통지 메일 3통(17:22 경고 · 17:24 shutting-down · 17:31 terminated) |
| 18:40 | 세션이 인스턴스 목록에서 발견했다(메일 3통은 그때까지 읽지 않은 상태였다) |
| 18:50 | 사용자 확인 뒤 기동 — `i-09f6b4f41ebd973c7`(스팟 one-time · 2d · 시세 $0.4649/h · 루트 120 GiB · 태그 `role=k3s-agent-gpu`) |
| 18:52 | 조인(`ip-172-31-60-217` Ready, 라벨·테인트 확인) → 3·4·5 단계 |
| 18:56 | `ollama-gpu` 1/1 · 모델 2종 · 엔드포인트 ready |

- 5 단계 뒤 ArgoCD(automated·selfHeal)가 PVC 를 7초 안에 다시 만들었고 새 PV 가 새 노드에 붙었다.
  이번에는 PVC 와 함께 Pending 이던 대체 파드도 지웠다(그게 필요했는지는 따로 가르지 않았다).
- 생성 속도: `qwen2.5:3b` **103 tok/s** · `qwen2.5:7b` **52 tok/s**(방금 받은 모델이라 첫 로드가 1.8초였다).
  GPU 노드에 뜬 파드는 `ollama-gpu` 와 device plugin 둘뿐이다.
- ai-svc 는 회수 동안 폴백 래치를 연 채 Claude 재시도를 유지했다
  (`provider liveness probe failed … latchOpen=true`, 17:56·18:26·18:56 — 30분 간격).
  18:56:09 프로브는 파드가 Ready 가 되기(18:56:21) 12초 전이라 실패했다.
- 중단 시간: 17:22 → 18:56, 약 94분.

### 루트 볼륨 (2026-10-06 실측·결정)

GPU 노드(`ip-172-31-52-85`, AMI 기본 75 GiB)의 루트가 **64G/73G(88%)** 였고, 이미지 GC 임계(85%)를 넘어 kubelet 이
`FreeDiskSpaceFailed` 를 2026-10-01 12:45Z 부터 1,366회(약 5분 간격) 냈다. 컨테이너 이미지는 4.6 GiB 뿐이라 이미지 GC 로는 줄지 않는다.

| 경로 | 크기 | 내용 |
|---|---|---|
| `/usr/local` | 41G | AMI 에 들어 있는 CUDA 툴킷 4벌 — `cuda-12.8` 11G · `12.9` 12G · `13.0` 9G · `13.2` 9G |
| `/var/lib/rancher/k3s` | 15G | agent 8G + `ollama-gpu-models` PV 7G(`qwen2.5:7b`·`3b`) |
| `/var/lib/kubelet` | 7G | |

과반이 AMI 기본 탑재물이고 워크로드는 22G 안팎이다. 축출 임계(kubelet `evictionHard` = `nodefs.available`·`imagefs.available` 5%)에는
닿지 않았다(여유 12%).

**결정(2026-10-06)**: 살아 있는 노드는 그대로 두고, **다음 기동부터 120 GiB** 로 띄운다(「절차」 2). 노드는 회수 때마다
AMI 에서 다시 만들어지므로 고칠 곳은 기동 절차다. gp3 는 서울 **$0.0912/GB-월**(2026-10-06 Pricing API) — 75→120 GiB 는 월 약 $4.1 다.
`growpart`·`resizefs` 모듈은 이 AMI 의 `/etc/cloud/cloud.cfg` 에 켜져 있고, 2026-10-01 첫 부팅 로그에 `growpart` 실행 기록이 있다.
위 매핑은 2026-10-06 `RunInstances` DryRun(같은 AMI·타입·서브넷·SG·키, 스팟 one-time)으로 수락되는 것을 확인했다.
**첫 120 GiB 기동 실측(2026-10-06 18:50Z, `i-09f6b4f41ebd973c7`)**: `/dev/root` **117G** — 첫 부팅 직후 49G(43%),
이미지와 모델 2종을 받은 뒤 64G(55%)·여유 53G. 이미지 GC 임계(85%) 아래다.

참고(미사용): g6.xlarge 의 인스턴스 스토어가 AMI 에 의해 `/opt/dlami/nvme`(LVM·ext4, 229G 중 28K 사용)로 마운트돼 있다.
정지·회수 때 사라지는 디스크라 지금은 쓰지 않는다.

GPU 노드 SSH 는 공인 IP 로 직접 붙는다. control-plane 경유(ProxyCommand)는 타임아웃이었다 — 노드 간 SG 규칙(「절차」 1)에 22 가 없다.

### 회수 탐지 — EventBridge → SNS (2026-10-02 구축·검증)

23일 무인지의 직접 원인은 회수 자체가 아니라 **알려 주는 경로가 하나도 없었다**는 것이다.
클러스터에는 여전히 모니터링 스택이 없고(monitoring 네임스페이스 없음·CronJob 0개), 대신
**클러스터를 건드리지 않는 AWS 측 통지**를 붙였다. 리전은 `ap-northeast-2`.

| 리소스 | 이름 / ARN |
|---|---|
| SNS 토픽 | `arn:aws:sns:ap-northeast-2:963773969059:devpath-spot-interruption` |
| 규칙 ① 사전 경고 | `devpath-spot-interruption-warning` — `EC2 Spot Instance Interruption Warning` + `EC2 Instance Rebalance Recommendation` |
| 규칙 ② 회수 완료 | `devpath-instance-stopped-or-terminated` — `EC2 Instance State-change Notification` 중 `terminated`·`stopped`·`shutting-down` |
| 규칙 ③ 미복구 반복 | `devpath-gpu-node-absence-watch` — `rate(6 hours)` → Lambda(아래 「미복구 반복 통지」, 2026-10-06 추가) |
| 구독 | email `deepestdark@gmail.com` — **확인 완료**(`ConfirmSubscription`) |

★**규칙을 둘로 나눈 이유**★ — 회수 2분 전 경고만으로는 23일 방치가 다시 일어난다. 그 2분에
사람이 없으면 아무 일도 안 생기기 때문이다. ②가 「회수가 끝났다 = 복구가 필요하다」를 알린다.
②는 인스턴스를 가리지 않는데(이벤트에 태그가 없어 태그 필터가 불가능하다) 계정에 인스턴스가
2대뿐이라 노이즈가 아니라 이득이다 — control-plane 정지도 알아야 한다.

타깃은 `InputTransformer` 로 사람이 읽는 문장을 만든다(원본 JSON 이 메일로 오면 쓸모가 없다).
본문에 복구 절차 링크와 ★새 노드만 띄우면 Pending 그대로★ 경고를 함께 넣었다.

검증(둘 다 실측):

- `TestEventPattern` 매칭 행렬 — 규칙①은 경고·재균형에 `true`, `terminated` 에 `false`;
  규칙②는 그 반대. **음성 대조군 `state=running` 은 두 규칙 모두 `false`**(정상 가동을 알리지 않는다).
- `sns:Publish` 로 종단 전달 확인.

**남은 공백**: Slack 수신처가 없다 — 사용자 결정은 「이메일·Slack 둘 다」였고 이메일만 완료됐다.
「회수된 뒤 복구되지 않은 상태」의 반복 통지는 2026-10-06 에 채웠다(아래 「미복구 반복 통지」).

| 방안 | 범위 | 비고 |
|---|---|---|
| Slack 추가 수신처 | SNS → AWS Chatbot(Slack) 또는 SNS → Lambda → Incoming Webhook | 2026-10-02 기록은 「Chatbot API 엔드포인트 연결 차단」이었고, 2026-10-06 에는 AWS MCP `call_boto3` 로 `DescribeSlackWorkspaces`(us-east-2)가 호출돼 승인된 워크스페이스 0개를 돌려줬다. 남은 것은 콘솔의 워크스페이스 OAuth 승인(또는 Webhook URL 발급)이고 사람이 해야 한다 |
| 스팟 ASG(capacity 1)로 자동 재기동 | 토큰을 SSM SecureString + 인스턴스 프로파일로 옮겨야 한다 | 런북 3번(「user-data 에 토큰을 넣지 않는다」)의 재설계가 필요 |

### 미복구 반복 통지 — EventBridge 일정 → Lambda → SNS (2026-10-06 구축·검증)

규칙 ②는 회수를 한 번만 알린다. 그 메일을 놓치면 다시 조용해진다. 이 층은 GPU 인스턴스가 없는 동안 6시간마다 같은 토픽으로 알린다.

| 리소스 | 이름 |
|---|---|
| 규칙 ③ | `devpath-gpu-node-absence-watch` — `rate(6 hours)`. 만든 직후 한 번 돌았고(2026-10-06 18:46Z) 그 뒤 6시간 간격이다 |
| Lambda | `devpath-gpu-node-absence-watch` — python3.13 · arm64 · 256 MB · 타임아웃 30초. 소스 `infra/aws/gpu-node-absence-watch/handler.py`, 테스트 `tests/release/test_gpu_node_absence_watch.py` |
| 실행 역할 | `devpath-gpu-node-absence-watch` — `ec2:DescribeInstances` · 이 토픽에 대한 `sns:Publish` · 자기 로그 그룹 쓰기뿐 |
| 로그 | `/aws/lambda/devpath-gpu-node-absence-watch`(보존 30일) |

판정: 태그 `role=k3s-agent-gpu` 를 가진 `pending`·`running` 인스턴스가 **하나도 없으면** 알린다. 그래서 새 노드는 반드시 그 태그로 띄운다(「절차」 2).

★**EC2 만 본다.** 인스턴스는 떠 있는데 조인하지 못했거나 파드가 Pending 인 상태는 알리지 않는다.
복구 뒤 검증(「스팟 회수 후 복구」 6·7)은 그대로 따로 한다.★

검증(둘 다 운영 계정에서 실측, 2026-10-06):

- 양성 — 구축 시점에 GPU 노드가 실제로 회수돼 있었다. 호출 결과 `{"gpu_instances": [], "notified": true}`, 메일 도착 18:46:35Z.
- 음성 대조군 — 새 노드를 띄운 뒤 `{"gpu_instances": ["i-09f6b4f41ebd973c7"], "notified": false}`, 메일 없음.
- 콜드 스타트 실행 시간: 128 MB 에서 7.5초, 256 MB 에서 4.0초.

코드를 바꿀 때는 테스트를 먼저 고치고(`python -m unittest discover -s tests/release -p 'test_gpu_node_absence_watch.py'`),
`handler.py` 하나를 zip 으로 묶어 `UpdateFunctionCode` 한다. AWS MCP `call_boto3` 는 바이트 인자(`ZipFile`)를 넘기지 못한다(2026-10-06 실측:
`Uploaded file must be a non-empty zip`) — 임시 비공개 S3 버킷에 presigned URL 로 올려 `S3Bucket`/`S3Key` 로 지정하고, 반영 뒤 버킷을 지운다.
배포된 코드가 저장소와 같은지는 `CodeSha256`(zip 의 sha256 을 base64 로)으로 본다. 2026-10-06 배포분은
`pkHhRBf8zgZTUA7Hy5/X+yJu9oYwvLWBaPEBv47Z6f4=` 다(`ZipInfo` 의 `date_time` 을 `2026-10-06 00:00:00` 으로 고정, deflate, 파일 하나).

### 함정

| 증상 | 원인 | 해법 |
|---|---|---|
| `MaxSpotInstanceCountExceeded` | 승인받은 것은 **온디맨드** G/VT 쿼터. 스팟은 `L-3819A6DF` 로 별개이고 기본 0 | 스팟 쿼터를 따로 증설 요청한다 |
| 새 노드가 `NotReady` 이거나 조인 실패 | SG 에 클러스터 내부 규칙이 없다(기본 SG 는 22·6443 을 관리 IP 에만 연다) | 위 self-referencing 규칙 3개 |
| device plugin 파드가 Pending | 노드 테인트에 대한 톨러레이션 없음 | 매니페스트의 톨러레이션 확인 |
| 서버·에이전트 버전 불일치 | `INSTALL_K3S_VERSION` 미지정 시 최신이 설치된다 | 서버 버전으로 고정 |
| `kubectl rollout restart deploy/ollama-gpu` 가 끝나지 않는다 (2026-10-02 실측) | 노드의 `nvidia.com/gpu` 가 1개뿐이라 새 파드는 `Insufficient nvidia.com/gpu` 로 Unschedulable 이고, 롤아웃은 그 새 파드를 기다리므로 옛 파드를 끝내지 않는다 — **영구 교착** | 매니페스트에 `strategy: Recreate` 를 넣었다. 이미 교착됐으면 `kubectl rollout undo` 로 풀고, 파드만 새로 띄우려면 `kubectl delete pod` 를 쓴다(순차 진행이라 교착하지 않는다) |
| Ollama 모델 로드가 100초 넘게 걸린다 (2026-10-02 실측) | `limits.memory: 8Gi` 안에서 모델을 셋 이상 다루면 page cache 가 한도를 채운다. `memory.events` 의 `max` 가 94,449회인데 `oom_kill` 은 0 — **OOM 이 아니라 회수 압박**이라 죽지 않고 느려진다. 런너가 `Dl`(uninterruptible I/O)로 남아 파드 종료도 막는다 | 모델을 2종(`qwen2.5:3b`·`qwen2.5:7b`)으로 유지한다. 모델을 지워도 cache 는 안 빠지므로, 이미 포화됐으면 cgroup 이 초기화되도록 **파드를 재생성**한다 |
