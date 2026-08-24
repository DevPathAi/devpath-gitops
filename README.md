# devpath-gitops

**DevPath AI** 의 Kubernetes 매니페스트 + 배포 설정(GitOps)입니다. ArgoCD가 이 레포를 단일 소스로 삼아 클러스터 상태를 동기화합니다.

## 구조

```
devpath-gitops/
├── apps/                       # 서비스별 K8s 매니페스트 (Kustomize)
│   ├── devpath-gateway/base/
│   ├── devpath-platform-svc/base/
│   ├── devpath-learning-svc/base/
│   ├── devpath-community-svc/base/
│   ├── devpath-ai-svc/base/
│   ├── devpath-sandbox-svc/base/
│   └── devpath-frontend/base/
├── argocd/                     # ArgoCD ApplicationSet + AppProject
│   ├── applicationset.yaml     # apps/* 자동 발견
│   └── project.yaml
├── staging/devpath-web/        # Mission Spine 전용 격리 staging (수동 최초 부트스트랩)
├── infra/                      # 인프라 애드온 (추후: ingress, kafka, monitoring 등)
└── local-k8s/                  # 로컬 클러스터(kind) 안내
```

## 동작 방식

`argocd/applicationset.yaml`이 `apps/*` 디렉터리를 자동 발견하여 각 서비스를 ArgoCD Application으로 배포합니다. 새 서비스는 `apps/<name>/base/`에 Kustomize 매니페스트를 추가하면 됩니다.

각 서비스 base 구성:

| 파일 | 내용 |
|------|------|
| `deployment.yaml` | Deployment (이미지 `ghcr.io/devpathai/<svc>:latest`, actuator health probe) |
| `service.yaml` | ClusterIP Service |
| `kustomization.yaml` | 리소스 묶음 |

## 부트스트랩

```bash
# 1. ArgoCD 설치 (클러스터에 최초 1회)
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# 2. DevPath 프로젝트 + ApplicationSet 등록
kubectl apply -f argocd/project.yaml
kubectl apply -f argocd/applicationset.yaml
```

이후 `main`에 푸시하면 ArgoCD가 자동 동기화(prune + selfHeal)합니다.

### Mission Spine staging 부트스트랩

`staging/devpath-web`은 production ApplicationSet의 `apps/*` 대상이 아니며 ArgoCD가 관리하지 않습니다. `mission-spine-staging` 환경의 `devpath-staging` context에 최초 한 번만 다음 선행조건을 확인하고 적용합니다.

```bash
# namespace만 먼저 만들고, secret 값은 저장소나 명령행에 넣지 않습니다.
kubectl --context devpath-staging apply -f staging/devpath-web/namespace.yaml
# 아래 secret은 이 사이에 별도 보안 절차로 생성되어 있어야 합니다.
kubectl --context devpath-staging --namespace devpath-staging \
  get secret mission-spine-synthetic-probe -o name
kubectl --context devpath-staging apply -k staging/devpath-web
kubectl --context devpath-staging --namespace devpath-staging \
  rollout status deployment/devpath-web-staging --timeout=300s
```

최초 적용 뒤 Deployment image와 release identity의 소유자는 보호된 Mission Spine workflow입니다. 일반 운영 중 `kubectl apply -k staging/devpath-web`을 다시 실행하면 봉인된 staging 기준점을 초기값으로 되돌릴 수 있으므로 사용하지 않습니다. 후보 검증은 매 전환을 `resourceVersion` 선행조건으로 수행하고 원래 prior로 복원합니다. production mission-ON 성공 뒤에는 별도 staging job이 새 ON lineage로 기준점을 갱신하고, production reverse rollback 뒤에는 별도 staging job이 봉인된 prior lineage로 되돌립니다. 두 경로 모두 같은 `mission-spine-staging` 승인 환경과 concurrency lease를 사용합니다. 매니페스트 변경은 main 봉인 정책의 보호 대상입니다.

## 로컬 검증

```bash
# Kustomize 렌더링 확인
kubectl kustomize apps/devpath-gateway/base
kubectl kustomize staging/devpath-web
```

로컬 클러스터 기동은 [local-k8s/README.md](local-k8s/README.md) 참고.

## 관련 문서

- Mission Spine 영구 릴리스 계약: [release-manifests/README.md](release-manifests/README.md)
- 2026-08-17 운영 핸드오프: [docs/mission-spine-release-handoff-2026-08-17.md](docs/mission-spine-release-handoff-2026-08-17.md)
- Mission Spine 문서 인덱스와 외부 산출물 보존 기록: [docs/mission-spine/README.md](docs/mission-spine/README.md)
- 아키텍처: [documents/03_프로젝트_아키텍처_정의서](https://github.com/DevPathAi/documents/blob/main/03_프로젝트_아키텍처_정의서.md)
- 배포 가이드: [documents/14_배포_가이드](https://github.com/DevPathAi/documents/blob/main/14_배포_가이드.md)
