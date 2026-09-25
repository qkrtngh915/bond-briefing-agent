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

## Phase 3 — 에이전트 툴 및 리포트 규격

### 3-1 신규 툴
`agent/tools_schema.py`에 `get_credit_snapshot`, `get_issuance_market_summary`,
`get_ktb_supply` 3개 추가. `get_foreign_flows`는 Phase 2-2에서 데이터 소스가
전부 막혀 구현 자체가 불가능하므로 **추가하지 않음** (스펙에 있던 4개 중 3개만
구현, WORK_LOG에 사유 기록 — 위 Phase 2-2 참고).
- `get_credit_snapshot(date)`: `analytics/credit.py: calc_credit_context`를
  호출하되, lookback을 스펙의 1년(252영업일)이 아니라 **60영업일(약
  3개월)로 고정**해서 호출함 — 252영업일 순차 호출은 브리핑 1회당 4분+
  걸려 인터랙티브 에이전트 툴로 쓰기엔 너무 느림 (Phase 1-2에서 이미
  검증된 값과 동일한 타협). 툴 설명에 "최근 약 3개월 기준"이라고 명시하도록
  요구함.
- `get_issuance_market_summary(date, window_business_days=20)`:
  `analytics/issuance.py: calc_issuance_summary` 그대로 연결. LLM 호출 0회.
- `get_ktb_supply(month)`: `tools/ktb_supply.py: get_issuance_plan` 그대로
  연결. 툴 설명에 "개별 입찰 낙찰금리/응찰률은 조회 불가 - 지어내지 말 것"을
  명시.

### 3-2 코드 기반 해석 라벨
`analytics/curve.py`에 `classify_curve_label(date)` 추가 — 국고 3-10년
구간의 불/베어(방향) × 스티프닝/플래트닝(기울기) 라벨을
`CURVE_LABEL_PARALLEL_THRESHOLD_BP`(1bp) 기준으로 코드에서 고정 판정.
가짜 데이터 단위테스트 3개로 검증(시드된 50bp 점프 → "스티프닝", 평소
날짜 → "보합", 잘못된 날짜 → calc_daily_changes와 동일하게 ValueError
전파).

크레딧 스프레드 regime(타이트/중립/와이드) 라벨은 이미 Phase 1-2
`calc_credit_context`에 구현되어 있어 추가 작업 없음.

외국인 수급 방향 분류기(`FOREIGN_FLOW_NEUTRAL_BAND` 소비)는 Phase 2-2에서
데이터 소스가 없어 **만들지 않음** — config의 placeholder 값은 죽은 채로
남겨둠.

### 3-3 시스템 프롬프트 개편
`agent/prompts.py`의 `SYSTEM_PROMPT`를 전면 재작성:
- 섹션 순서를 스펙대로 1 한줄요약+커브라벨 / 2 금리동향 / 3 국채수급 /
  4 크레딧 / 5 특이사항 / 6 체크포인트로 변경 (크레딧이 6번에서 4번으로
  이동, 국채수급이 신규 3번으로 삽입).
- "모든 숫자에 비교 기준(전일/전주/percentile 중 최소 1개) 필수" 규칙과
  각 섹션 끝 "운용 시사점"(매매 추천 아님, 관찰 포인트) 규칙을 명시적으로
  추가.
- 근거 없는 인과 서술 금지 규칙을 "수급/뉴스상 뚜렷한 원인 확인 안 됨"이라는
  고정 문구로 통일 (get_bond_demand_forecasts/get_ktb_supply에서 실제 근거가
  나온 경우만 예외).
- 라벨(curve_label/regime/direction/temperature)은 코드가 준 값을 그대로
  인용하고 LLM이 재판정하지 말라는 규칙 추가.
- 국채수급 섹션에는 "외국인 수급 데이터 소스 없음"을 명시적으로 쓰도록
  지시 (Phase 2-2 스킵 결정을 리포트 차원에서도 정직하게 드러내기 위함).
- 자리표시자 숫자(X.XX% 등, 실제 값처럼 안 보이게)로 된 좋음/나쁨 예시
  문장 6쌍 추가.
- `get_market_snapshot`의 dispatch 결과에 `curve_label`(Phase 3-2의
  `classify_curve_label`) 필드를 추가해서, 에이전트가 별도 툴 호출 없이
  스냅샷 하나로 한줄요약용 커브 라벨까지 받게 함.

`agent/llm_backend.py`의 `build_demo_mock_script`(verify.py 데모용 mock
스크립트, 5섹션 구 포맷)는 그대로 둠 — SYSTEM_PROMPT를 참조하지 않고
verify.py의 숫자 불일치 검출 자체만 시연하는 독립된 테스트 픽스처라 새
6섹션 포맷과 맞출 필요가 없음 (tests/test_loop.py는 섹션 헤더가 아니라
tool_call_order/검증 경고 문자열만 확인).

## Phase 4 — 품질 평가

`scripts/eval_report.py` 작성 (LLM 호출 없음, 리포트 텍스트 + `logs/{date}.jsonl`만
읽어서 코드로 채점): 비교기준 없는 숫자 비율 / 섹션별 "운용 시사점" 누락 /
근거 없는 인과 서술 개수 / verify.py 불일치 개수(로그의 마지막 `run_end`
이벤트에서 읽음). `tests/test_eval_report.py` 11개로 검증.

기존 reports/logs의 2026-09-11/09-21/09-23을 `docs/before_reports/`,
`docs/before_logs/`에 백업(Before 스냅샷)한 뒤, `LLM_BACKEND=anthropic`으로
같은 3개 날짜를 실제로 재생성(브리핑 실행 3회 - 이 작업의 6회 예산 중 3회
사용, 나머지 0회는 추가로 쓰지 않음)해서 `reports/`/`logs/`를 덮어씀(After).

채점기 자체의 버그/한계 2건을 실제 데이터로 발견해서 코드로 고침:
1. "판단"/"추정"처럼 명시적 인과 접속사("때문"/"영향") 없이도 근거 없는
   해석을 서술하는 패턴을 인과 마커에 추가.
2. `annotate_with_warnings`가 붙이는 "## ⚠ 검증 경고" 섹션 자체의 문구가
   인과 서술로 오탐되는 문제 - 채점 전에 그 섹션을 잘라내도록 수정.
둘 다 회귀 테스트 추가(`test_evaluate_report_ignores_appended_verify_warning_section`
등).

`docs/quality_before_after.md`에 지표 비교표 + 크레딧 섹션 정성적
before/after 발췌 + Phase 4에서 새로 발견된(아직 안 고친) 한계 3가지
(프롬프트 서두 위반, 뉴스 근거 부분 과잉해석, 비교기준 100% 강제는 안 됨)를
정직하게 기록. 결론: Phase 0이 지적한 4개 문제 중 3개(비교기준/크레딧
피상성/국채수급 부재)는 정량적으로 개선됐고, 근거 없는 인과 서술은 완전히
해결되지 않음(모델 자체 경향으로 보임, 추가 튜닝 필요 지점으로 남김).

## Phase 5 — 문서 갱신

`README.md`: 아키텍처 다이어그램에 신규 툴/analytics 모듈(credit.py,
issuance.py, ktb_supply.py) 반영, "해석 라벨도 코드가 고정한다"/"모든
숫자에 비교 기준을 강제한다" 설계 포인트 추가, "데이터 소스" 표 신설
(KAP/ECOS 191Y001 포함), "조사했지만 못 쓴 소스" 섹션 신설(KRX 로그인
게이트, KOFIA FreeSIS 역공학 불가, ktb.moef.go.kr/data.go.kr 사유),
"리포트 품질 평가" 섹션 신설(Before/After 요약 표 + `docs/quality_before_after.md`
링크), "한계"에 외국인 수급 없음/국고채 개별 입찰 없음/크레딧 lookback
단축/Haiku 형식 위반 4개 항목 추가, 폴더 구조에 신규 파일 전부 반영.

`ROADMAP.md`: #1(금투협 스프레드)과 #3(국고채 입찰)을 "부분 구현됨"으로
갱신하고 실제 구현 현황+남은 과제를 적음. #2(국채선물 basis/외국인 수급)는
"무료 지연 데이터 vs 유료 실시간"이라는 옛 추측을, Phase 2-2에서 실제로
확인한 "KRX API가 로그인 필요로 바뀜"이라는 사실로 교체하고 구현 난이도를
"중간"에서 "사실상 유료 단말/라이선스 필수"로 상향 조정. 인턴 우선순위
가이드도 이 변화를 반영해서 갱신(#3/#7 → #7 우선, #2는 유료 경로부터
확인하라고 명시).

## 최종 요약

### 단계별 완료/스킵 현황 + 커밋 해시

| Phase | 상태 | 커밋 |
|---|---|---|
| 0. 기존 리포트 품질 진단 | 완료 | `b4b16d3` |
| 1-1/1-2/1-3. 크레딧 데이터 확장 | 완료 | `5fe8c8b` |
| 2-1. 국고채 입찰 결과 | **부분 완료** (월별 발행 실적만, 개별 입찰 상세는 스킵) | `0d72a31` |
| 2-2. 외국인 수급 | **스킵** (fallback 없음, 지침대로) | `ae10870` |
| 2-3. 국채선물 basis | **스킵** (2-2와 동일 원인, 1시간 타임박스 안 채우고 즉시 스킵) | `ae10870` |
| 3-1. 신규 에이전트 툴 | 완료 (4개 스펙 중 3개 - get_foreign_flows 제외) | `d168aaa` |
| 3-2. 코드 기반 해석 라벨 | 완료 (커브 라벨; 크레딧 regime/방향은 Phase 1-2에 이미 있었음; 외국인 수급 라벨은 데이터 없어 스킵) | `d168aaa` |
| 3-3. 시스템 프롬프트 개편 | 완료 | `a72bcc7` |
| 4. 품질 평가 | 완료 (eval_report.py + 실제 브리핑 3회 + before/after 문서) | `a461285` |
| 5. 문서 갱신 | 완료 (README, ROADMAP) | `710cdac` |

### 데이터 소스별 성공/폴백/스킵

| 데이터 | 결과 | 이유 |
|---|---|---|
| 등급/섹터별 민평 스프레드 | **성공 (대체 소스)** | KOFIA 자체 사이트는 죽음 → KAP(koreaap.com) 공개 AJAX로 대체, ECOS 시장금리와 교차검증해서 신뢰성 확인 |
| 발행시장 집계(수요예측 기반) | **성공** | 이미 있는 DART 캐시만 재사용, LLM 호출 0회 |
| 국고채 발행 규모 | **부분 성공(fallback)** | 1차(기재부 사이트)·2차(data.go.kr) 모두 막혀 ECOS 191Y001(월별 실적만)로 축소 |
| 국고채 개별 입찰 결과(낙찰금리/응찰률) | **스킵** | 위와 동일 - ECOS엔 없는 데이터라 구현 자체를 안 함 |
| 외국인 채권 수급 | **스킵(fallback 없음)** | KRX는 로그인 게이트 확인, 금투협 FreeSIS는 역공학 불가 확인 - 둘 다 실제로 조사해서 막힌 것 확인, 추측이 아님 |
| 국채선물 basis | **스킵** | 외국인 수급과 동일한 KRX API가 막혀서 파생됨 |

### 실제 API 호출 횟수 (이번 Phase E 작업, 2026-09-25 진행분)

- **Anthropic(Haiku) 브리핑 실행**: 3회 (2026-09-11, 2026-09-21, 2026-09-23) / 예산 6회 중 3회 사용, 3회 미사용.
- **DART 수요예측 구조화 추출(Anthropic 호출)**: Phase 1 검증용 백필(`backfill_extract.py --no-dry-run`, 7일/14건) 14회 + Phase 4 브리핑 3회 실행 중 신규 추출 5건 = **총 19회**. 이 항목은 공통 규칙상 "기존 캐시 규칙 그대로"라 별도 상한이 없었고, 전부 영구 캐시(`data/extracted/`)에 남아 다음 실행부터는 재호출되지 않음.
- **ECOS/FRED/KAP 등 비-LLM 데이터 API 호출**: 이번 작업 전체(Phase 1/2 조사·검증 포함)에서 수십~백여 회 수준(각 툴의 로컬 일별 캐시 덕분에 같은 날 재실행 시엔 재호출 안 됨) - 비용은 발생하지 않는 무료 공개 API라 별도로 세지 않았음.

### 사용자가 직접 검증해야 할 숫자

- **등급/섹터별 민평 스프레드(KAP)**: `koreaap.com`에서 가져온 값. ECOS 시장금리와 근접해서 교차검증은 했지만, KAP는 이번에 처음 편입한 소스라 실제 채권 스크린(블룸버그/인포맥스 등)과 한 번은 직접 대조해보는 걸 권장.
- **국고채 월별 발행 실적(ECOS 191Y001)**: 월별 집계 자체는 신뢰할 만한 소스(한국은행)지만, "발행 계획 대비 진행률"이 아니라 "전년 동월 대비 YoY"로 재정의했다는 점을 반드시 인지하고 읽을 것 - 원래 기대했던 의미(계획 대비 진행률)가 아니다.
- **DART 수요예측 추출값(경쟁률/가산금리/발행금액)**: `_validation.flags`가 붙은 회차는 이미 "검증 필요"로 표시되지만, 플래그가 없는 회차도 LLM 추출이므로 중요한 투자판단에 쓰기 전엔 원문 공시와 한 번은 대조 권장.
- **국고채 개별 입찰 결과·외국인 수급**: 이 두 가지는 리포트 어디에도 숫자로 등장하지 않는다(의도적으로 스킵됨) - 혹시 리포트를 읽다가 이 항목이 암시적으로라도 언급된 것처럼 보이면 그건 모델의 서술 오류이니 반드시 의심할 것.
- **크레딧 스프레드 percentile**: "최근 약 3개월(60영업일)" 기준이라고 리포트에 명시하도록 프롬프트에 못박아뒀지만, 스펙이 원했던 "1년" 기준과는 다르다는 점을 감안해서 읽을 것.
