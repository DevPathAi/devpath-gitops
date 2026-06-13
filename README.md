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

## 로컬 검증

```bash
# Kustomize 렌더링 확인
kubectl kustomize apps/devpath-gateway/base
```

로컬 클러스터 기동은 [local-k8s/README.md](local-k8s/README.md) 참고.

## 관련 문서

- 아키텍처: [documents/03_프로젝트_아키텍처_정의서](https://github.com/DevPathAi/documents/blob/main/03_프로젝트_아키텍처_정의서.md)
- 배포 가이드: [documents/14_배포_가이드](https://github.com/DevPathAi/documents/blob/main/14_배포_가이드.md)
