# 실 운영(베타) 배포 환경 — AWS + k3s 설계

- 날짜: 2026-07-05
- 레포: devpath-gitops(배포 주도) + 각 svc(CI 이미지)
- 상태: 브레인스토밍 승인됨

## 배경 / 목표

DevPath MVP(④②③ 완료, ① 결제 잔여)를 **베타/데모로 실 배포**한다. AWS vs Railway를 검토해 **AWS + 경량 K8s(k3s on EC2)**를 선정했다.

## 결정 사항 (브레인스토밍)

- **단계**: 베타/데모(소규모 실사용자·데모, 추후 실운영 전환 염두).
- **예산**: $50~200/월.
- **플랫폼**: AWS EC2 + **k3s + ArgoCD** — 현재 gitops 매니페스트를 그대로 재사용.
  - **Railway 기각 근거**: K8s/ArgoCD·SealedSecret 자산 폐기, Kafka 외부 의존, 8-svc MSA를 PaaS로 관리, 실운영 전환 시 재이주. 이벤트 기반 MSA와 마찰.
- **Stateful**: Postgres(pgvector)=**RDS**(managed, 백업·복구), Kafka=**self-host**(Strimzi, 이벤트 백본), Redis=**self-host**(캐시), Elasticsearch=**보류**(실사용 코드 최소).
- **실운영 전환**: k3s → EKS(동일 ArgoCD 매니페스트 재사용, stateful을 MSK/RDS/ElastiCache로 승격).

## 확정 사실 (현 자산)

- gitops = **K8s + ArgoCD ApplicationSet**(`argocd/applicationset.yaml`, `apps/*` 자동 발견, revision `main`, namespace `devpath`). 8 svc(platform·ai·community·gateway·learning·sandbox·admin·web).
- **Kafka가 이벤트 백본**: platform·community·ai·sandbox·learning·notification 전 svc가 Outbox+Consumer(아웃박스→Kafka→소비).
- **SealedSecret 구조 준비됨**(platform deployment env `secretKeyRef: platform-oauth` optional + `docs/sealed-secrets-oauth.md`), 컨트롤러는 미설치.
- 이미지 레지스트리 = `ghcr.io/devpathai/*`.

## 컴포넌트

### C1. EC2 + k3s
- AWS EC2(ap-northeast-2 서울), **t3.xlarge**(4vCPU/16GB) 1대(베타 single-node) — 8 svc(경량 Spring Boot) + Strimzi Kafka + self-host Redis 감안. Elastic IP 부착.
- k3s(single-node, 내장 Traefik·local-path-provisioner). 실운영 시 노드 추가/EKS 전환.

### C2. ArgoCD + ApplicationSet
- k3s에 ArgoCD 설치. 현재 `argocd/applicationset.yaml`·`project.yaml`을 이 클러스터에 적용 → `apps/*` 자동 배포.
- **revision을 `main`으로 유지**(배포 기준). develop→main 릴리스 PR로 배포 반영.

### C3. Kafka (self-host, Strimzi)
- Strimzi Kafka Operator를 `kafka` namespace에 설치. 베타는 **단일 브로커 + 단일 ZK/KRaft**. 각 svc `spring.kafka.bootstrap-servers`를 클러스터 내 서비스 DNS로.
- AppProject `devpath`는 ns 제한이라 Operator(cluster 리소스)는 별도 설치(SealedSecret 컨트롤러와 동일 패턴).

### C4. RDS Postgres(pgvector)
- RDS PostgreSQL(db.t4g.micro), `pgvector` 확장 활성화. 각 svc `DB_URL`을 RDS 엔드포인트로(SealedSecret).
- 스키마: 현행대로 devpath-shared 중앙 Flyway 마이그레이션(배포 파이프라인 또는 마이그레이션 Job).

### C5. Redis (self-host)
- k3s에 Redis(단일, PVC). 각 svc `REDIS_HOST`를 클러스터 내 서비스로.

### C6. 네트워킹 / TLS
- k3s 내장 **Traefik ingress** + **cert-manager**(Let's Encrypt). 도메인(예: `api.<domain>`·`app.<domain>`) → Elastic IP. DNS는 Route53 또는 외부.
- gateway(devpath-gateway)가 API 진입점, web/admin은 정적 서빙.

### C7. 시크릿
- **SealedSecret 컨트롤러 설치**(helm bitnami, kube-system) → OAuth(`platform-oauth`)·DB·Kafka 자격을 봉인 커밋. `docs/sealed-secrets-oauth.md` 절차 재사용/확장.

### C8. CI/CD
- 각 svc CI가 ghcr 이미지 push → **ArgoCD Image Updater**(태그 자동 갱신) 또는 현 방식(CI가 gitops kustomization 태그 커밋). ArgoCD 자동 sync(prune·selfHeal).

## 토폴로지 / 데이터 흐름

```
[사용자] → DNS → EIP → k3s Traefik ingress (TLS/cert-manager)
  → gateway → 각 svc(platform/ai/community/learning/sandbox/notification)
      ├─ RDS Postgres(pgvector)   [managed]
      ├─ Redis (k3s self-host)
      └─ Kafka (Strimzi, k3s)  ← Outbox→발행 / Consumer→소비 (svc 간 이벤트)
  web/admin(Flutter Web) ← Traefik 정적 서빙
ArgoCD ← gitops(main) 감시 → apps/* 동기화
```

## 비용 추정 (서울, 베타)

- EC2 t3.xlarge ~$120/월 · RDS db.t4g.micro ~$15/월 · EIP/전송/EBS 소액 = **~$130~180/월** (예산 내).
- 절감 옵션: t3.large로 축소, 야간 중지(데모).

## 실운영 전환 경로

- k3s → **EKS**: 동일 ArgoCD ApplicationSet/매니페스트 재사용. stateful을 **MSK(Kafka)·RDS Multi-AZ·ElastiCache(Redis)**로 승격, 노드 오토스케일. 앱 매니페스트는 대부분 불변.

## 리스크

- **R1 single-node HA 없음**: 베타 허용(데모). 실운영 전 노드 추가/EKS.
- **R2 self-host stateful 운영**: Kafka/Redis 백업·복구·모니터링 직접. Postgres는 RDS로 완화.
- **R3 k3s/EC2 관리 부담**: OS 패치·k3s 업그레이드·디스크. 문서화 필요.
- **R4 실제 프로비저닝은 AWS 계정·비용 발생**: 계정·결제·도메인 준비 필요(사용자).

## 범위 밖

- 멀티리전·오토스케일·실운영 HA(전환 시).
- 관측성 스택(Prometheus/Grafana/Loki)은 후속.
- ① 결제(토스) 관련 인프라(별도 spec).
