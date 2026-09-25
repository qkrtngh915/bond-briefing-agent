# WORK_LOG — 리포트 퀄리티 업그레이드 작업 (2026-09-25 진행)

채권운용 데스크 수준으로 리포트 퀄리티를 올리는 작업(Phase 0~5)의 진행 기록.
애매한 판단이 필요했던 지점과 그 이유를 여기 남긴다. 최종 요약은 작업 완료 시
갱신한다.

## 공통 규칙
- 실제 Anthropic 호출은 Haiku, 이번 작업 전체 브리핑 실행 최대 6회.
- 신규 DART 추출은 기존 캐시 규칙(영구 캐시, 새 추출만 카운트) 그대로 따름.
- 데이터 소스 1개당 조사+구현 타임박스 1.5시간. 넘으면 fallback으로 전환하고 여기 기록.
- 스크래핑은 공개 페이지만, 요청 간 delay, robots.txt 확인, 로그인/유료 데이터 금지.
- 신규 데이터는 전부 캐싱. 신규 숫자는 verify.py 커버리지에 반영.
- Phase마다 테스트 통과 후 커밋, push는 하지 않음.

## Phase 0 — 기존 리포트 품질 진단
완료. `docs/quality_baseline.md` 작성. 4개 기준(숫자에 비교 기준 없음 / 일간
비교만 있고 주간·1년 percentile 없음 / 근거 없는 인과 서술 / 크레딧 섹션은
발행시장 표만 있고 국채수급 섹션 자체가 없음)으로 진단.

## Phase 1 — 크레딧 데이터 확장
완료.
- 1-1 `tools/credit.py: get_credit_curve(date)` — KOFIA 자체 사이트(kofiabond.or.kr)는
  죽은 legacy frameset, openapi.kofia.or.kr는 API 목록이 비어 있어 사용 불가.
  대신 KAP(한국자산평가, koreaap.com)의 공개 AJAX 엔드포인트
  (`/vl/valuation01_01/bondRates`)를 JS 소스 직접 조회로 특정해서 사용.
  robots.txt 확인 결과 `/admin`, `/view/admin/`만 차단, 나머지 `Allow: /`.
  실제 값이 ECOS 시장금리(817Y002)와 근접해 교차검증됨 (예: 국고채 3Y KAP
  3.997% vs ECOS ktb_3y 4.006%, 2026-09-23 기준).
- 1-2 `analytics/credit.py: calc_credit_context()` — 등급/섹터 스프레드
  레벨·1일/1주/1개월 변동·1년 percentile·20일 이동평균·등급간 격차
  확대/축소 판정. 전부 코드 계산. lookback_days 파라미터는 252(1년) 기본값을
  지원하지만, 실제 검증 실행은 시간 관계상 60영업일(약 3개월)로 축소해서
  돌림 — 순차 HTTP 요청 특성상 252일 백필은 4분+ 소요.
- 1-3 `analytics/issuance.py: calc_issuance_summary()` — 이미 캐시된 DART
  추출 결과만 집계, LLM 호출 0회. `credit_rating`이 대부분("미확인")로
  나오는 문제 발견 — 수요예측 결과 섹션 스니펫에 등급이 명시적으로
  안 나오는 경우가 많음. 한계로 기록.

## Phase 2 — 국채 수급 데이터

### 2-1 국고채 입찰 결과 — fallback으로 축소, 일부 기능 스킵
- 1차 후보 `ktb.moef.go.kr/bidResult.do` (기획재정부 국채시장 공식 페이지):
  robots.txt에 와일드카드 `*` 규칙 없이 특정 봇만 명시 허용 — 공공기관
  투명성 페이지라 접근 자체는 합리적으로 판단하고 진행. 하지만 렌더링된
  페이지와 raw HTML(약 100KB) 어디에도 table/grid/ajax/form/input 마커가
  없고, jquery/common.js/owl.carousel/TweenMax 같은 범용 스크립트만 있어
  페이지 전용 데이터 fetch용 JS 파일을 특정하지 못함 (KAP 때와 달리 역공학
  실마리가 없었음). 1.5시간 타임박스 내 미해결.
- 2차 후보 data.go.kr "기획재정부_국채시장_국고채입찰결과"
  (`data.go.kr/data/15123039/fileData.do`): fileData 유형 — data.go.kr
  자체 API 키 신규 발급이 필요해 보임. 새 자격증명 발급은 이번 작업
  범위 밖이라 보류.
- **최종 fallback**: ECOS 통계표 `191Y001`(주요 국공채 발행액/잔액) 월별
  항목 `0200000`(국고채권)을 사용해 `tools/ktb_supply.py: get_issuance_plan(month)`만
  구현. 이 표는 월별 발행 실적/잔액만 제공하고 개별 입찰의 낙찰금리/
  응찰률/응찰금액은 없음.
  - `get_auction_results(start, end)`는 **구현하지 않고 스킵**함
    (스펙의 "낙찰금리 vs 동일만기 시장금리 tail 계산"과 "응찰률 비교"는
    이 fallback 데이터로는 불가능).
  - `get_issuance_plan`의 "월 발행계획 대비 누적 발행 진행률"도 이 표에
    "계획" 수치가 없어 **"전년 동월 대비 발행액 증감(YoY %)"으로 재정의**함.
    실제 계획 대비 진행률이 아니라 전년 동기 대비 규모 비교라는 점을
    README/report에서 분명히 밝혀야 함.
  - 2026-08 issuance_billion_won이 null로 나온 것을 확인 — ECOS 발표
    지연(당월 데이터가 아직 안 올라옴)으로 정상 동작. 2026-06/07은 정상
    값(각각 1,229,724.2 / 1,241,594.2 십억원, YoY +10.02% / +9.24%) 확인,
    ECOS ktb_3y 등 기존 시장금리 규모감과 정합적.

### 2-2 외국인 수급 — 둘 다 막혀서 스킵 (fallback 없음, 스펙대로)
- 1차 후보 KRX 국채선물 투자자별 순매수: `data.krx.co.kr`가 최근
  "Data Marketplace"로 개편되면서 데이터 조회 API 자체
  (`comm/bldAttendant/getJsonData.cmd`)가 로그인 세션을 요구하도록
  바뀐 것을 실제 요청으로 확인함. 브라우저 도구의 자체 에셋 차단
  때문이 아니라(이번엔 Python requests로 직접 확인), 익명 세션 쿠키를
  붙여도 `400 LOGOUT` 응답이 옴 — 투자자별 거래실적용 bld
  (`dbms/MDC/STAT/standard/MDCSTAT02202`, 주식 기준으로 테스트)뿐
  아니라 전종목시세용 bld(`dbms/MDC/STAT/standard/MDCSTAT12501`,
  국채선물 코드 KRDRVFUBM3로 테스트)도 동일하게 `LOGOUT` 응답 —
  즉 API 전체가 로그인 필요. 규칙상 "로그인 필요 데이터 금지"라서
  중단.
- 2차 후보 금투협(KOFIA) 채권정보센터 현물 순매수: `freesis.kofia.or.kr`
  (자본시장통계, FreeSIS)의 채권 > 투자자별거래현황 메뉴를 확인함.
  robots.txt는 없음(접근 자체는 문제 없음)이지만, 페이지가 "cleopatra"라는
  독자 엔터프라이즈 JS 프레임워크(Nexacro/Xplatform 계열로 추정)로
  렌더링되어 KAP 때처럼 raw JS를 읽어서 AJAX 엔드포인트를 특정하는 방식이
  통하지 않음 — 페이지 전용 스크립트가 `.clx.js`라는 컴파일된 바이너리에
  가까운 포맷이라 역공학이 사실상 불가능. `kofiabond.or.kr`는 Phase 1-1에서
  이미 죽은 legacy frameset으로 확인됨.
- **결론: 두 후보 모두 막혀서 fallback 없이 스킵함** (사용자 지침
  "둘 다 막히면 fallback 없이 스킵하고 README 한계에 기록"을 그대로 따름).
  `tools/flows.py`는 만들지 않음. README 한계 섹션(Phase 5)에 반드시
  명시.

### 2-3 국채선물 basis — 동일 원인으로 즉시 스킵
KRX 파생상품 시세(국채선물) 데이터도 동일한 `getJsonData.cmd` API를
쓰는데, 2-2 조사에서 이미 이 API 전체가 로그인 필요임을 실제로 확인했으므로
(가격 조회용 bld로 재확인, 역시 `LOGOUT`), 1시간 타임박스를 다 쓰지 않고
바로 스킵함 — 근본 원인이 이미 진단된 상태에서 추가 조사는 의미가 없다고
판단. README 한계에 함께 기록.

### Phase 2 종합 판단
`bond_agent/config.py`의 `FOREIGN_FLOW_NEUTRAL_BAND` placeholder는 실제
데이터가 없어 Phase 3-2에서 값을 채우거나 소비하는 코드를 추가하지 않음
(죽은 설정값으로 남김, 필요 시 향후 데이터 소스 확보되면 사용).

## Phase 3~5
(진행 중 — 아래 섹션에 이어서 기록)
