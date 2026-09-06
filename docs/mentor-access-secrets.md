# 멘토 접근 운영 Secret

이번 릴리스의 멘토 접근 코드 검증에는 아래 Kubernetes Secret이 먼저 있어야 한다.
참조는 의도적으로 `optional`이 아니다. 테스트용 HMAC 키로 운영 pod가 기동하는 것을
막기 위해서다.

| Secret | key | 사용처 |
|---|---|---|
| `mentor-access` | `invite-code-hmac-secret` | 초대 코드 HMAC-SHA256(최소 32바이트) |

운영 클러스터의 Sealed Secrets 공개키를 사용할 수 있는 환경에서만 암호화 파일을
만든다. 평문 Secret YAML이나 실제 키는 Git에 추가하지 않는다.

```powershell
$inviteKeyBytes = New-Object byte[] 48
[Security.Cryptography.RandomNumberGenerator]::Fill($inviteKeyBytes)
$inviteKey = [Convert]::ToBase64String($inviteKeyBytes)

kubectl -n devpath create secret generic mentor-access `
  --from-literal=invite-code-hmac-secret=$inviteKey `
  --dry-run=client -o json |
  kubeseal --controller-namespace kube-system `
    --controller-name sealed-secrets-controller --format yaml `
  > apps/devpath-platform-svc/base/sealedsecret-mentor-access.yaml
```

SMTP 자격정보가 준비되지 않아 메일 발송 설정은 이번 릴리스에서 제외한다. 실제 SMTP
Secret을 봉인하고 notification 서비스 배포에서 참조하도록 구성한 뒤에만 후속
릴리스에 포함한다.
