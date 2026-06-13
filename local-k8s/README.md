# 로컬 Kubernetes (kind)

로컬에서 GitOps 배포를 검증할 때 사용합니다.

## kind 클러스터 생성

```bash
kind create cluster --name devpath
```

## ArgoCD 설치 + 등록

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl apply -f ../argocd/project.yaml
kubectl apply -f ../argocd/applicationset.yaml
```

## ArgoCD UI 접속

```bash
kubectl -n argocd port-forward svc/argocd-server 8080:443
# 초기 비밀번호
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
```

브라우저에서 https://localhost:8080 접속 (admin / 위 비밀번호).

## 정리

```bash
kind delete cluster --name devpath
```

> 애플리케이션 이미지(`ghcr.io/devpathai/*`)는 각 서비스 CI가 빌드·푸시합니다. 로컬에서 직접 빌드한 이미지를 쓰려면 `kind load docker-image <image>`로 적재하세요.
