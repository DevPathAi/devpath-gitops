# 공개 지원·멘토 접근 운영 Secret

이번 증분 릴리스의 공개 지원과 멘토 접근에는 아래 Kubernetes Secret이 먼저 있어야
한다. 세 참조는 의도적으로 `optional`이 아니다. 테스트용 키나 누락된 검증 설정으로
운영 pod가 기동하는 것을 막기 위해서다.

| Secret | key | 사용처 |
|---|---|---|
| `platform-turnstile` | `turnstile-secret` | `leva.ai.kr` 공개 문의의 Turnstile 서버 검증 |
| `platform-public-support` | `rate-limit-hmac-secret` | 공개 문의 rate-limit 식별자 HMAC-SHA256(최소 32바이트) |
| `mentor-access` | `invite-code-hmac-secret` | 초대 코드 HMAC-SHA256(최소 32바이트) |

운영 클러스터의 Sealed Secrets 공개키를 사용할 수 있는 환경에서만 암호화 파일을
만든다. 평문 Secret YAML이나 실제 키는 Git에 추가하지 않는다.

### 멘토 초대 코드 HMAC

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

### 공개 문의 rate-limit HMAC과 Turnstile

`platform-public-support`도 독립적으로 생성한 최소 32바이트 키를
`rate-limit-hmac-secret`으로 봉인한다. 멘토 초대 코드 키와 같은 값을 재사용하지 않는다.

`platform-turnstile`에는 Home의 production site key와 같은 Turnstile widget에서 발급된
server secret을 봉인해야 한다. 이번 증분 PR은 live widget 생성과 자격정보 연결을 포함하지
않는다. 실제 widget·site key·server secret의 짝과 hostname `leva.ai.kr`을 확인하기 전에는
production 배포를 진행하지 않는다. Mission Spine staging provisioner가 넣는
`disabled-mission-staging-turnstile` 값은 비운영 검증 전용이며 production으로 복사하지 않는다.

SMTP 자격정보가 준비되지 않아 메일 발송 설정은 이번 릴리스에서 제외한다. 실제 SMTP
Secret을 봉인하고 notification 서비스 배포에서 참조하도록 구성한 뒤에만 후속
릴리스에 포함한다. 그때까지 `MENTOR_INVITE_BATCH_ENABLED=false`를 유지한다.
