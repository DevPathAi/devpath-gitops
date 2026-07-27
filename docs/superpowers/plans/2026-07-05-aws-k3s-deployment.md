# AWS k3s 베타 배포 구축 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. 단계는 체크박스(`- [ ]`). **주의: 이 계획의 실행은 AWS 계정·결제·도메인이 필요하다(사용자 게이팅).** 각 단계는 코드 테스트가 아니라 **인프라 명령 + 상태 검증(kubectl/argocd/aws)**으로 완료를 확인한다.

**Goal:** DevPath MVP를 AWS EC2 + k3s + ArgoCD 위에 베타/데모로 배포한다(현 gitops 매니페스트 재사용, Postgres=RDS, Kafka/Redis=self-host).

**Architecture:** 서울 리전 EC2 t3.xlarge 1대에 k3s(single-node) → ArgoCD가 gitops `main`의 `apps/*`를 자동 배포. Kafka는 Strimzi(kafka ns), Redis는 k3s self-host(devpath ns), Postgres는 RDS. 시크릿은 SealedSecret. Traefik+cert-manager로 TLS.

**Tech Stack:** AWS(EC2·RDS·EIP) · k3s · ArgoCD · Strimzi · SealedSecret · cert-manager · Traefik

## Global Constraints

- 리전 **ap-northeast-2(서울)**. EC2 **t3.xlarge**(4vCPU/16GB) single-node(베타). 예산 ~$130~180/월.
- ArgoCD ApplicationSet **revision `main`**, 앱 namespace **`devpath`**(현 `argocd/applicationset.yaml`·`project.yaml` 그대로).
- Postgres = **RDS**(db.t4g.micro, pgvector). Kafka = **Strimzi self-host**(`kafka` ns, 베타 단일 브로커). Redis = **self-host**(`devpath` ns). **Elasticsearch 보류**.
- 시크릿 = **SealedSecret**(`kube-system` 컨트롤러). **평문 미커밋**(CLAUDE.md).
- AppProject `devpath`는 ns 제한 → cluster 리소스(Strimzi/SealedSecret/cert-manager Operator)는 **ArgoCD 밖에서 직접 설치**.
- 검증: 각 Task 끝에 `kubectl get`/`argocd app get`/`aws` 상태로 확인.

---

## File Structure (gitops 신규/수정)

- Create `infra/redis/base/{deployment.yaml,service.yaml,kustomization.yaml}` — self-host Redis(devpath ns)
- Create `infra/cert-manager/cluster-issuer.yaml` — Let's Encrypt ClusterIssuer
- Create `apps/*/base/deployment.yaml`(수정) — 각 svc에 `DB_URL`·`KAFKA_BOOTSTRAP`·`REDIS_HOST` env(secretKeyRef/configMap)
- Create `docs/runbook-k3s-bootstrap.md` — 클러스터 부트스트랩 명령 모음(이 plan의 요약본)
- 재사용: `argocd/applicationset.yaml`·`project.yaml`, `apps/devpath-platform-svc/base/deployment.yaml`(OAuth env 이미 있음), `docs/sealed-secrets-oauth.md`

---

## Task 1: AWS 기반 리소스 프로비저닝 (사용자, 콘솔/CLI)

**검증 산출물:** SSH 가능한 EC2 + Elastic IP + RDS 엔드포인트.

- [ ] **Step 1: EC2 인스턴스 생성**

AWS 콘솔 또는 CLI(서울):
```bash
aws ec2 run-instances --region ap-northeast-2 \
  --image-id <ubuntu-22.04-ami> --instance-type t3.xlarge \
  --key-name <keypair> --security-group-ids <sg> \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=50}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=devpath-k3s}]'
```
보안그룹 인바운드: 22(SSH, 본인 IP), 80/443(HTTP/HTTPS, 0.0.0.0/0), 6443(k3s API, 본인 IP).

- [ ] **Step 2: Elastic IP 할당·연결**

```bash
aws ec2 allocate-address --region ap-northeast-2 --domain vpc
aws ec2 associate-address --region ap-northeast-2 --instance-id <id> --allocation-id <eipalloc>
```

- [ ] **Step 3: RDS PostgreSQL 생성 (pgvector)**

```bash
aws rds create-db-instance --region ap-northeast-2 \
  --db-instance-identifier devpath-pg --engine postgres --engine-version 17 \
  --db-instance-class db.t4g.micro --allocated-storage 20 \
  --master-username devpath --master-user-password <STRONG_PW> \
  --db-name devpath --vpc-security-group-ids <sg-rds> --backup-retention-period 7
```
- 보안그룹 `sg-rds`: 인바운드 5432 ← EC2 보안그룹만 허용.
- 생성 후 psql로 `CREATE EXTENSION IF NOT EXISTS vector;` (각 필요 DB/스키마).

- [ ] **Step 4: 검증**

`aws ec2 describe-instances`로 running·EIP 확인, `aws rds describe-db-instances`로 available·엔드포인트 확인. `ssh ubuntu@<EIP>` 접속 확인.

---

## Task 2: k3s 설치 (EC2)

**검증 산출물:** `kubectl get nodes` Ready.

- [ ] **Step 1: k3s 설치**

EC2에서:
```bash
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--write-kubeconfig-mode 644" sh -
```
(k3s는 Traefik·local-path-provisioner·CoreDNS 내장.)

- [ ] **Step 2: kubeconfig 확보**

```bash
sudo cat /etc/rancher/k3s/k3s.yaml   # server: https://127.0.0.1:6443 → <EIP>로 교체해 로컬에 저장
```
로컬에서 `export KUBECONFIG=~/.kube/devpath-k3s.yaml`.

- [ ] **Step 3: 검증**

```bash
kubectl get nodes            # Ready
kubectl get pods -A          # traefik·coredns·metrics-server Running
```

---

## Task 3: ArgoCD 설치 + gitops 연결

**검증 산출물:** ArgoCD가 `apps/*`를 Application으로 발견·동기화 시작.

- [ ] **Step 1: ArgoCD 설치**

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd rollout status deploy/argocd-server
```

- [ ] **Step 2: 프로젝트·ApplicationSet 적용 (현 gitops 재사용)**

```bash
kubectl apply -f argocd/project.yaml
kubectl apply -f argocd/applicationset.yaml
```

- [ ] **Step 3: 검증**

```bash
kubectl get applicationset -n argocd devpath-services
argocd app list    # (argocd CLI 로그인 후) platform·ai·community… 앱 목록
```
> 이 시점엔 시크릿/인프라 미완이라 일부 앱이 Degraded/Progressing일 수 있다(Task 4~8에서 해소).

---

## Task 4: SealedSecret 컨트롤러 + 시크릿 봉인

**검증 산출물:** `platform-oauth` 등 Secret이 컨트롤러로 복호화 생성.

- [ ] **Step 1: 컨트롤러 설치**

```bash
helm repo add sealed-secrets https://bitnami-labs.github.io/sealed-secrets
helm install sealed-secrets sealed-secrets/sealed-secrets -n kube-system
kubectl -n kube-system rollout status deploy/sealed-secrets
```

- [ ] **Step 2: 자격 SealedSecret 생성** (`docs/sealed-secrets-oauth.md` 절차)

`platform-oauth`(github/google client), `platform-db`(DB_URL/USER/PW), `platform-kafka`(bootstrap) 등을 kubeseal로 봉인해 `apps/*/base/sealedsecret-*.yaml`로 커밋. 예:
```bash
kubectl create secret generic platform-db -n devpath \
  --from-literal=db-url='jdbc:postgresql://<rds-endpoint>:5432/devpath' \
  --from-literal=db-user=devpath --from-literal=db-password='<PW>' \
  --dry-run=client -o yaml \
  | kubeseal --controller-namespace kube-system --controller-name sealed-secrets -o yaml \
  > apps/devpath-platform-svc/base/sealedsecret-db.yaml
```
`kustomization.yaml`의 resources에 각 sealedsecret 추가.

- [ ] **Step 3: 검증**

커밋·push→main 반영 후 ArgoCD sync → `kubectl get secret -n devpath platform-oauth platform-db` 존재 확인.

---

## Task 5: Strimzi Kafka (self-host)

**검증 산출물:** `kafka` ns에 브로커 Ready, 부트스트랩 서비스 DNS.

- [ ] **Step 1: Operator 설치**

```bash
kubectl create namespace kafka
kubectl create -f 'https://strimzi.io/install/latest?namespace=kafka' -n kafka
kubectl -n kafka rollout status deploy/strimzi-cluster-operator
```

- [ ] **Step 2: Kafka 클러스터 매니페스트 적용 (베타 단일 브로커)**

`kafka/kafka-cluster.yaml`:
```yaml
apiVersion: kafka.strimzi.io/v1beta2
kind: Kafka
metadata:
  name: devpath
  namespace: kafka
spec:
  kafka:
    version: 3.9.0
    replicas: 1
    listeners:
      - name: plain
        port: 9092
        type: internal
        tls: false
    config:
      offsets.topic.replication.factor: 1
      transaction.state.log.replication.factor: 1
      transaction.state.log.min.isr: 1
      default.replication.factor: 1
      min.insync.replicas: 1
    storage: {type: persistent-claim, size: 10Gi, deleteClaim: false}
  zookeeper:
    replicas: 1
    storage: {type: persistent-claim, size: 5Gi, deleteClaim: false}
  entityOperator: {topicOperator: {}, userOperator: {}}
```
```bash
kubectl apply -f kafka/kafka-cluster.yaml
kubectl -n kafka wait kafka/devpath --for=condition=Ready --timeout=300s
```

- [ ] **Step 3: 검증**

부트스트랩 서비스 = `devpath-kafka-bootstrap.kafka.svc:9092`. 각 svc `KAFKA_BOOTSTRAP`을 이 값으로(Task 7 env). `kubectl -n kafka get kafka,pods` Ready 확인.

---

## Task 6: Redis (self-host)

**검증 산출물:** `devpath` ns에 Redis Running, 서비스 DNS.

- [ ] **Step 1: 매니페스트 작성 + 커밋**

`infra/redis/base/deployment.yaml`(redis:7-alpine, PVC), `service.yaml`(`redis:6379`), `kustomization.yaml`. `apps/*`는 ApplicationSet이 devpath ns에 배포하므로 redis도 `apps/redis/base`로 두면 자동 발견된다(경로 통일 권장: `apps/devpath-redis/base`).

```yaml
# apps/devpath-redis/base/deployment.yaml (요지)
apiVersion: apps/v1
kind: Deployment
metadata: {name: redis, labels: {app: redis}}
spec:
  replicas: 1
  selector: {matchLabels: {app: redis}}
  template:
    metadata: {labels: {app: redis}}
    spec:
      containers:
        - name: redis
          image: redis:7-alpine
          ports: [{containerPort: 6379}]
          volumeMounts: [{name: data, mountPath: /data}]
      volumes: [{name: data, persistentVolumeClaim: {claimName: redis-data}}]
```
(+ PVC `redis-data`, Service `redis`.)

- [ ] **Step 2: 검증**

push→main→ArgoCD sync → `kubectl -n devpath get pods -l app=redis` Running. 서비스 DNS `redis.devpath.svc:6379`.

---

## Task 7: 각 svc 인프라 env 배선 (DB/Kafka/Redis)

**검증 산출물:** 각 svc가 RDS·Strimzi·Redis에 연결(pod Running, health OK).

- [ ] **Step 1: deployment env 추가 (svc별)**

각 `apps/<svc>/base/deployment.yaml`에 env 추가(값 출처: SealedSecret/상수):
```yaml
          env:
            - name: DB_URL
              valueFrom: {secretKeyRef: {name: platform-db, key: db-url}}
            - name: DB_USER
              valueFrom: {secretKeyRef: {name: platform-db, key: db-user}}
            - name: DB_PASSWORD
              valueFrom: {secretKeyRef: {name: platform-db, key: db-password}}
            - name: KAFKA_BOOTSTRAP
              value: "devpath-kafka-bootstrap.kafka.svc:9092"
            - name: REDIS_HOST
              value: "redis.devpath.svc"
```
> svc마다 필요한 것만(예: ai-svc는 CLAUDE_API_KEY SealedSecret 추가). platform은 OAuth env(이미 있음)와 병합.

- [ ] **Step 2: DB 마이그레이션 적용**

devpath-shared 중앙 Flyway를 RDS 대상으로 1회 실행(마이그레이션 Job 또는 로컬 `flyway migrate -url=<rds>`). 스키마 생성 확인.

- [ ] **Step 3: 검증**

push→main→sync → `kubectl -n devpath get pods` 전 svc Running, `kubectl -n devpath logs <pod>`에 DB/Kafka 연결 성공. `argocd app get <svc>` Healthy.

---

## Task 8: TLS + Ingress + DNS

**검증 산출물:** `https://<domain>`로 앱 접근, 유효 인증서.

- [ ] **Step 1: cert-manager 설치 + ClusterIssuer**

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
kubectl -n cert-manager rollout status deploy/cert-manager
```
`infra/cert-manager/cluster-issuer.yaml`:
```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: {name: letsencrypt}
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: <admin-email>
    privateKeySecretRef: {name: letsencrypt-key}
    solvers: [{http01: {ingress: {class: traefik}}}]
```

- [ ] **Step 2: DNS A 레코드**

`api.<domain>`·`app.<domain>`·`admin.<domain>` → EC2 Elastic IP (Route53 또는 외부 DNS).

- [ ] **Step 3: Ingress (Traefik) + TLS 주석**

gateway/web/admin 앞에 Ingress(host + `cert-manager.io/cluster-issuer: letsencrypt`, tls). `apps/*/base`에 ingress.yaml 추가.

- [ ] **Step 4: 검증**

`kubectl get certificate -A` Ready, `curl -I https://api.<domain>/actuator/health` 200 + 유효 TLS.

---

## Task 9: 엔드투엔드 스모크 검증

**검증 산출물:** OAuth 로그인 → 온보딩 → 핵심 플로우 동작.

- [ ] **Step 1: OAuth 콜백 URI 등록**

GitHub/Google OAuth 앱에 `https://api.<domain>/login/oauth2/code/{github|google}` 등록(`docs/sealed-secrets-oauth.md` 참조).

- [ ] **Step 2: 스모크**

`https://app.<domain>` 접속 → GitHub/Google 로그인 → 세션/게이트 → 진단·경로 등 핵심 플로우. `kubectl -n devpath logs`·`argocd app list`로 오류 없음 확인.

- [ ] **Step 3: 런북 커밋**

`docs/runbook-k3s-bootstrap.md`에 Task 1~9 명령 요약 + 트러블슈팅을 정리해 커밋. develop→main 릴리스 PR로 반영.

---

## Self-Review

**1. Spec coverage:** C1 EC2/k3s=Task1·2 / C2 ArgoCD=Task3 / C3 Strimzi Kafka=Task5 / C4 RDS=Task1·7 / C5 Redis=Task6 / C6 Traefik·cert-manager TLS=Task8 / C7 SealedSecret=Task4 / C8 CI ghcr→ArgoCD=Task3(자동 sync, Image Updater는 후속). 실운영 전환(§6)은 별도(범위 밖). 커버 완료.

**2. Placeholder scan:** `<EIP>`·`<domain>`·`<PW>`·`<rds-endpoint>`·`<admin-email>`·`<ami>` 등은 **실행 시점 사용자 확정값**(placeholder 아님, 변수 표기). 명령·매니페스트는 실제 값.

**3. 일관성:** `devpath-kafka-bootstrap.kafka.svc:9092`·`redis.devpath.svc`·`platform-db`/`platform-oauth` Secret 이름이 Task 4~8에서 일관. namespace(`devpath`/`kafka`/`kube-system`/`cert-manager`/`argocd`) 일관.

**리스크:** 실행은 AWS 계정·결제·도메인 필요(사용자 게이팅). single-node라 HA 없음(베타 허용). self-host Kafka/Redis 백업은 후속.
