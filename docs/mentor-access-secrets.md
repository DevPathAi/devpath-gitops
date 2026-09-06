# 멘토 초대 운영 Secret

멘토 일괄 초대 배포에는 아래 두 Kubernetes Secret이 먼저 있어야 한다. 두 참조는
의도적으로 `optional`이 아니다. 테스트용 HMAC 키나 mock 메일 발송기로 운영 pod가
기동하는 것을 막기 위해서다.

| Secret | key | 사용처 |
|---|---|---|
| `mentor-access` | `invite-code-hmac-secret` | 초대 코드 HMAC-SHA256(최소 32바이트) |
| `notification-mail` | `host`, `port`, `username`, `password` | 실제 SMTP 전송 |

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

메일 Secret도 같은 방식으로 `kubectl create secret generic notification-mail
--from-literal=... --dry-run=client -o json | kubeseal ...`로 봉인한다. 생성 후 각
`kustomization.yaml`의 `resources`에 해당 SealedSecret을 추가하고 렌더·서버
dry-run을 다시 실행한다.
