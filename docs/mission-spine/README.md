# Mission Spine 문서 인덱스

이 디렉터리는 Mission Spine의 설계·검토·릴리스 운영 문서를 한곳에서 찾기 위한 인덱스다. 릴리스 실행 계약의 권위 문서는 [`release-manifests/README.md`](../../release-manifests/README.md), 현재 운영 상태와 STOP 조건의 권위 문서는 [2026-08-17 핸드오프](../mission-spine-release-handoff-2026-08-17.md)다.

## 문서 지도

| 목적 | 문서 |
|---|---|
| 영구 릴리스 계약 | [`release-manifests/README.md`](../../release-manifests/README.md) |
| 현재 통합·운영 상태, 외부 설정, 병합 순서, STOP 조건 | [2026-08-17 릴리스 핸드오프](../mission-spine-release-handoff-2026-08-17.md) |
| 제품·디자인 결정의 원본 | [Mission Spine 디자인](supporting-artifacts/2026-08-15/mission-spine-design.md) |
| 엔지니어링 검증 계획의 원본 | [Mission Spine Engineering QA Plan](supporting-artifacts/2026-08-15/mission-spine-engineering-qa-plan.md) |
| 독립 적대 검토의 원본 응답 | [`independent-plan-review.json`](supporting-artifacts/2026-08-15/independent-plan-review.json)의 `result` 필드 |
| 설계·엔지니어링 작업 목록 | [`design-review-tasks.jsonl`](supporting-artifacts/2026-08-15/design-review-tasks.jsonl), [`engineering-review-tasks.jsonl`](supporting-artifacts/2026-08-15/engineering-review-tasks.jsonl) |
| 결정·학습·검토 이력 | [`decision-question-log.jsonl`](supporting-artifacts/2026-08-15/decision-question-log.jsonl), [`learnings.jsonl`](supporting-artifacts/2026-08-15/learnings.jsonl), [`review-summary.jsonl`](supporting-artifacts/2026-08-15/review-summary.jsonl) |
| 구현 리뷰 기록 | [`frontend-et7-review-log.jsonl`](supporting-artifacts/2026-08-15/frontend-et7-review-log.jsonl), [`frontend-et9-review-log.jsonl`](supporting-artifacts/2026-08-15/frontend-et9-review-log.jsonl) |
| 도구 실행 타임라인 | [`timeline.jsonl`](supporting-artifacts/2026-08-15/timeline.jsonl) 및 저장소별 `*-timeline.jsonl` |

## 보존 원칙

- `supporting-artifacts/2026-08-15/`의 16개 파일은 `D:\workspace\dpa` 밖의 `.gstack` 프로젝트 디렉터리에서 이동한 원본 바이트다.
- 파일명만 검색 가능한 이름으로 정규화했으며 파일 내용은 수정하지 않았다.
- `.gitattributes`의 `-text -diff` 규칙으로 Git의 줄바꿈 정규화와 텍스트 재해석을 끄고 원본 바이트를 보존한다.
- 이동 전후 SHA-256과 크기는 핸드오프의 “문서 정리 및 외부 산출물 이동” 절에 고정했다.
- JSON/JSONL은 감사·의사결정 보조 증거다. 현재 코드·CI·GitHub·환경 상태를 대신하는 운영 권위값으로 사용하지 않는다.
- 외부 임시 체크아웃에 있던 tracked 문서의 동일 사본은 내부 원본과 SHA-256이 같은지 확인했으며 중복 커밋하지 않았다.
- 비밀값, 전역 도구 설정, 패키지 캐시, 빌드 산출물은 이 문서 집합에 포함하지 않는다.

## 무결성 확인

PowerShell에서 다음 명령으로 보존 파일을 다시 해시할 수 있다.

```powershell
Get-ChildItem -LiteralPath docs\mission-spine\supporting-artifacts\2026-08-15 -File |
  Sort-Object Name |
  ForEach-Object {
    [pscustomobject]@{
      Name = $_.Name
      Bytes = $_.Length
      SHA256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
    }
  }
```
