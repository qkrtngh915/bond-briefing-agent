# DEPLOY_LOG — GitHub 배포 및 Actions 실행 (2026-09-25)

## repo URL

https://github.com/qkrtngh915/bond-briefing-agent (public, default branch `main`)

## 실행 결과

**성공, 재시도 0회.** `gh workflow run` → `gh run watch`로 1회차 실행이
그대로 성공했다 (run id `36088877186`, `date=2026-09-23 force=true`로
수동 트리거, 소요 3분 21초). 모든 스텝(Checkout / Install / 날짜 해석 /
영업일 체크 / 브리핑 실행 / 커밋+푸시)이 초록불이었고, 실패해서 로그를
보고 고친 적이 없다 - 재시도 원인 자체가 없었다.

끝에 뜬 두 annotation(Node.js 20 deprecated, ubuntu-latest → Ubuntu 26
migration 예고)은 GitHub 플랫폼 자체의 안내 메시지이고 이번 실행의
성공/실패와 무관하다.

## 러너 환경(해외 IP)에서 접근 실패한 데이터 소스

**없음.** 실행 로그의 `툴 호출 순서`가
`get_market_snapshot → get_anomalies → get_credit_snapshot →
get_issuance_market_summary → get_bond_demand_forecasts → get_ktb_supply`로,
6개 툴이 전부 에러 없이 끝까지 돌았다. 즉 ECOS, KAP(credit.py), DART,
FRED가 전부 GitHub 호스티드 러너(미국 IP 대역)에서 정상 접근됐다 -
로컬(한국 IP)에서 확인했던 것과 동일하게 작동했다. 참고로 이미
Phase E에서 설계 자체가 "KRX/금투협 FreeSIS/기재부는 애초에 안 쓴다"로
정해져 있었으므로, 이 세 곳이 해외에서 막히는지는 이번 검증 범위가
아니다(막혔어도 애초에 이 에이전트가 호출하지 않는 소스라 영향 없음).

생성된 리포트(`reports/2026-09-23.md`)에도 국채수급 섹션이 "개별 입찰
낙찰금리, 응찰률, 외국인 순매수 등의 상세 수급 자료는 현재 데이터
소스가 없어 확인할 수 없습니다"라고 정상적으로 한계를 명시했다 - 즉
"에러로 죽는" 대신 "데이터 없음을 서술"하는 설계가 실제 Actions
환경에서도 의도대로 동작함을 확인했다.

## API 호출 수

이번 1회 실행(2026-09-23, 이상치 없는 조용한 날): 메인 루프 3회 +
신규 DART 추출 0건(이미 로컬에서 캐시된 공시라 재추출 안 함) = **총 3회**.

**매일 스케줄 기준 월 예상 호출 수**: 평일에만 실행되고(cron
`0-4` = 일~목 UTC, KST 기준 월~금 07:30), 한국 공휴일은 `check_business_day.py`가
스킵시키므로 실제 실행일은 순수 주5일보다 약간 적다. 한 달 약
20~21영업일 기준으로 추정하면:

- **이번 세션에서 실제로 관측된 num_turns(메인 루프)**: 2026-09-11 = 3,
  2026-09-21 = 2, 2026-09-23(Actions) = 3. 전부 2~3회였고, 이상치가 있던
  09-11도 3회에 그쳤다 (search_news 등 조건부 툴 호출이 같은 턴에 여러
  개 묶여서 나가는 경우가 많아서, 툴 호출 개수가 늘어도 턴 수 자체는
  크게 안 늘어남).
- **낙관적(오늘처럼 조용한 날 + 신규 DART 공시 없음)**: 메인 루프
  3회/일 × 21일 = **약 63회/월**.
- **DART 신규 추출을 감안한 상한**: 하루 최대 5건까지 신규 추출되는
  날이 매일 있다고 가정하면(실제로는 캐시가 쌓일수록 이보다 훨씬
  적어짐, 오늘 실행은 0건이었음) 메인 루프 3회 + DART 5회 = 8회/일 ×
  21일 = **약 168회/월**.
- **현실적 추정**: 메인 루프 2~3회/일 + DART 신규 추출 평균 0~1건/일
  기준 **약 50~90회/월** — 위 두 시나리오는 각각 낙관/상한이고, 몇 주
  실제 운영 후 관측치로 다시 추정하는 게 정확하다.

Haiku 모델이라 호출 1회당 비용 자체는 낮지만(입력 대부분이 짧은 JSON
툴 결과), 정확한 월 비용은 실제 몇 주 운영해보고 관측된 평균으로
다시 추정하는 게 정확하다 - 위 숫자는 이번 1회 실행과 이 세션에서
로컬로 돌려본 몇 번의 실행 기록을 근거로 한 추정치다.

## 사용자가 GitHub 웹에서 직접 확인해야 할 것

1. **레포가 정말 public으로 의도한 게 맞는지** - `--public`으로 만들었고
   확인했지만(레포에 있는 코드/로그/리포트가 전부 공개된다는 뜻), 혹시
   나중에 private으로 바꾸고 싶으면 레포 Settings > General > Danger
   Zone에서 변경 가능.
2. **Settings > Secrets and variables > Actions**에서 `ECOS_API_KEY`,
   `FRED_API_KEY`, `DART_API_KEY`, `ANTHROPIC_API_KEY` 4개가 정확히
   등록됐는지 이름만 눈으로 재확인 (저는 `gh secret list`로 이름만
   확인했고 값은 볼 수도 없고 보지도 않음).
3. **repository variable `ANTHROPIC_MODEL`을 설정할지 여부** - 지금은
   설정 안 해도 워크플로가 자동으로 Haiku를 기본값으로 쓰지만, 나중에
   다른 모델로 바꾸고 싶으면 Settings > Secrets and variables >
   Actions > Variables 탭에서 추가하면 됨.
4. **Actions 탭에서 스케줄이 실제로 매 평일 07:30 KST(전날 22:30 UTC)에
   도는지** 내일 이후 며칠 지켜보기 - 이번엔 수동(workflow_dispatch)
   실행만 검증했고, cron 스케줄 자체가 실제로 트리거되는 건 아직
   못 봤다 (GitHub Actions 스케줄은 첫 등록 후 최대 몇 시간 지연될 수
   있다는 점도 참고).
5. **오늘 커밋된 `reports/2026-09-23.md`/`logs/2026-09-23.jsonl`이
   실제로 레포에 반영됐는지** - 이미 로컬에 `git pull`로 받아서
   확인했지만, 웹에서도 https://github.com/qkrtngh915/bond-briefing-agent/commits/main
   에서 `daily-briefing-bot`이 커밋한 `Daily briefing 2026-09-23`를
   직접 봐두면 좋음.
6. **README의 Actions 배지가 정상적으로 초록불로 뜨는지** - 배지
   이미지는 캐시 때문에 몇 분 지연될 수 있음.
