# 결정 기록 — web 신뢰 베이스 앵커의 mission 접미 허용은 항구 설계다 (2026-08-22)

## 배경

ET10 수동 GitOps 릴리스(2026-08-22)에서 gitops 픽스처의 `base_web_tag` 를 배포
이미지 태그(`5c5f3a90…-mission-on`)로 이동시키고, validator(`WEB_BASE_TAG`)와
schema-v1 의 순수 SHA40 패턴을 `^[0-9a-f]{40}(-mission-(off|on))?$` 로 완화했다.
당시 핸드오프는 「봉인 절차 복원 시 이 앵커 설계를 재검토할 것」을 남겼다.

## 재검토 실측 (2026-08-22)

- 운영 web Deployment image = `ghcr.io/devpathai/devpath-web:5c5f3a90…-mission-on`
  (태그형 selector), 파드 imageID digest = `sha256:6484ed32…`.
- 픽스처 `base_web_tag`·`base_web_digest`·`frontend.rollback.prior_digest` 가 위
  운영 실물과 삼중 일치.
- 소비 지점 의미론:
  - `set_web_digest.py` — 승격 전 gitops main 의 현재 web selector 가 봉인된
    베이스(태그형이면 `base_web_tag`, digest 형이면 `base_web_digest`)와 일치해야
    진행하고, 교체는 **digest 형**으로만 쓴다.
  - `validate_release_manifest.py` — `rollback.prior_digest == base_web_digest` 강제.
  - `wait_web_rollout.py` — "prior" 단계에서 태그형 출발을 1회 허용하되 런타임
    imageID 는 반드시 prior digest 로 해석되어야 한다.
  - `verify_promotion_chain.py` — newTag==base 태그, 실패 시 digest==base digest.

## 결정

**완화된 패턴을 항구 설계로 비준한다.** 되돌리지 않는다.

1. **신뢰의 실체는 digest 다.** 모든 하드 앵커(rollback 동등성·selector 교체·
   런타임 검증)는 `base_web_digest` 에 걸려 있다. 태그는 사람이 읽는 라벨이자
   태그형 selector 에서 출발하는 전환 1회의 교차검증 값이다.
2. **mission 접미는 구조적이다.** 미션 스파인 흐름이 산출·배포하는 web 태그는
   `<sha40>-mission-(off|on)` 이다. 순수 SHA40 강제는 흐름의 실제 산출물과
   모순이며, 수동 경로가 아니라 봉인 경로에서도 첫 전환의 베이스는 현 운영
   태그(`…-mission-on`)다.
3. **자연 소멸 경로.** 봉인 승격은 selector 를 digest 형으로 교체하므로, 다음
   릴리스부터 베이스 검증은 digest 분기를 타고 태그 분기는 레거시 전환
   1회용으로만 남는다.
4. **`-off` 를 제한하지 않는다.** `-mission-on` 만 허용하면 승격 중단 사고 후
   rollback-first 를 강제하는 성격이 생기지만, digest 가 유일 신뢰원인 이상
   보안 이득이 없고 비상 시 선택지만 좁힌다.

## 반영 위치

- `scripts/release/validate_release_manifest.py` `WEB_BASE_TAG` 주석 (이 문서 링크)
- `release-manifests/schema-v1.json` `base_web_tag.description`
- 픽스처·테스트는 ET10 시점 값으로 이미 정합 (변경 없음)
