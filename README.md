# 데일리 채권시장 브리핑 에이전트

채권운용 데스크용 모닝 코멘트 생성 에이전트. `anthropic` Python SDK로 직접
구현한 tool-use 루프가 아래 툴들을 호출해서 브리핑을 생성한다 (Claude Agent
SDK는 사용하지 않음 — in-process MCP 대신, Messages API의 tool_use를 직접
처리하는 방식). LLM 호출부는 인터페이스로 분리되어 있어서, API 키 없이
`MockLLMBackend`로 전체 흐름을 테스트할 수 있다.

## 설계 원칙

- **숫자는 전부 코드로 계산한다.** LLM은 해석/서술과 "어떤 툴을 호출할지"만
  담당하고, 숫자를 스스로 계산하거나 추정하지 않는다.
- 리포트에 등장하는 모든 %/bp 숫자는 [agent/verify.py](agent/verify.py)가 실제
  툴 결과와 대조해서 불일치하면 경고 섹션을 붙인다 (마지막 안전망).
- 툴 함수는 순수 함수(입출력이 JSON 직렬화 가능한 dict/list)로 만든다.
- API 키는 `.env`에서 읽는다 (`ECOS_API_KEY`, `FRED_API_KEY`, `DART_API_KEY`,
  `ANTHROPIC_API_KEY`).
- 같은 날 재호출 시 API를 다시 부르지 않도록 `data/cache/`에 로컬 캐시한다
  (DART 원문은 접수번호별로 영구 캐시 - 내용이 바뀌지 않으므로).
- 모든 툴 호출/결과는 `logs/YYYY-MM-DD.jsonl`에 한 줄씩 기록한다 (에이전트가
  어떤 순서로 어떤 툴을 호출했는지 나중에 확인하기 위함).
- LLM 백엔드는 [agent/llm_backend.py](agent/llm_backend.py)의 `LLMBackend`
  인터페이스로 추상화되어 있다. `LLM_BACKEND=mock`(기본값)이면 API 키 없이
  미리 정해둔 tool_use 시퀀스를 재생하는 `MockLLMBackend`를 쓰고,
  `LLM_BACKEND=anthropic`이면 실제 Anthropic Messages API를 호출한다
  (`AnthropicLLMBackend`, 비용 발생).

## 폴더 구조

```
bond_agent/
  tools/
    ecos.py      # 한국은행 ECOS: 기준금리, 국고채 3/5/10/30년, 회사채 AA-/BBB- 3년
    fred.py      # FRED: DGS2, DGS10, SOFR
    news.py      # get_market_events(한은 RSS), search_news(연합/한경 RSS 검색)
    dart.py      # OpenDART: 회사채 증권신고서(채무증권) 목록 + 원문(수요예측 섹션)
    _cache.py    # 로컬 파일 캐시 헬퍼
  analytics/
    curve.py     # 전일 대비 변동(bp), 스프레드, 이상치(z-score) 계산
  config.py       # 경로, API 키, ECOS 통계표/항목 코드, LLM_BACKEND, ANTHROPIC_MODEL
agent/
  prompts.py       # 시스템 프롬프트 (역할/규칙/출력 형식, 6개 섹션)
  llm_backend.py    # LLMBackend 인터페이스: MockLLMBackend / AnthropicLLMBackend
  tools_schema.py   # Anthropic Messages API tool 정의(JSON Schema) + dispatch
  loop.py           # tool-use 루프 (최대 10회 반복), logs/*.jsonl 기록
  extract.py        # 증권신고서 원문 -> 수요예측 구조화 추출 (LLM, JSON Schema 강제 + 코드 검증)
  verify.py         # 리포트의 %/bp 숫자를 툴 결과와 대조하는 검증 레이어
scripts/
  lookup_ecos_codes.py         # ECOS StatisticTableList/ItemList 조회 (코드 확인용)
  demo_today.py                # 최근 영업일 기준 변동/스프레드/이상치 표 출력 (LLM 호출 없음)
  run_briefing.py              # 에이전트 루프를 돌려 reports/YYYY-MM-DD.md 생성
  review_bond_extractions.py   # 최근 공시 몇 건의 원문+추출결과를 나란히 출력 (DART_API_KEY 필요)
tests/
  test_ecos.py           # 실제 API 스모크 테스트
  test_fred.py            # 실제 API 스모크 테스트
  test_news.py             # 실제 RSS 스모크 테스트 (get_market_events, search_news)
  test_dart.py             # dart.py 파싱/캐시 단위 테스트 (가짜 응답, API 키 불필요)
  test_curve.py            # 가짜 데이터로 계산 로직 단위 테스트
  test_tools_schema.py     # agent 툴 스키마/dispatch 단위 테스트 (모델 호출 없음)
  test_loop.py             # MockLLMBackend로 tool-use 루프/로그/검증 흐름 전체 테스트
  test_extract.py          # 구조화 추출 + 코드 검증(_validate) 단위 테스트 (MockLLMBackend)
  test_verify.py           # 검증 레이어 단위 테스트 (모델 호출 없음)
data/cache/       # API 응답 로컬 캐시 (날짜별 + dart/ 하위는 접수번호별 영구 캐시)
logs/             # 에이전트 실행 로그 (YYYY-MM-DD.jsonl)
reports/          # 생성된 모닝 브리핑 (YYYY-MM-DD.md)
```

## 설치

```bash
python -m pip install -r requirements.txt
```

## 환경 설정

`.env.example`을 복사해서 `.env`를 만들고 키를 채운다.

```bash
cp .env.example .env
```

- `ECOS_API_KEY`: https://ecos.bok.or.kr 에서 발급
- `FRED_API_KEY`: https://fred.stlouisfed.org/docs/api/api_key.html 에서 발급
- `DART_API_KEY`: https://opendart.fss.or.kr 에서 발급 (회사채 수요예측 공시 조회용.
  **아직 미발급** — 키가 없으면 `get_bond_demand_forecasts` 툴과
  `scripts/review_bond_extractions.py`만 못 쓰고, 나머지는 정상 동작한다.)
- `LLM_BACKEND`: `mock`(기본값, 비용 없음) 또는 `anthropic`(실제 호출, 비용 발생)
- `ANTHROPIC_API_KEY`: https://console.anthropic.com 에서 발급.
  `LLM_BACKEND=anthropic`일 때만 필요.
- `ANTHROPIC_MODEL` (선택): 생략 시 `claude-sonnet-5` 사용

## 사용한 ECOS 통계표/항목 코드

`scripts/lookup_ecos_codes.py`로 직접 조회해서 확인한 값 (2026-09-24 기준,
`bond_agent/config.py`의 `ECOS_SERIES_DEFS`에 근거 주석과 함께 정리되어 있음):

| 시리즈 | 통계표 | 항목코드 | 이름 |
|---|---|---|---|
| base_rate | 722Y001 | 0101000 | 한국은행 기준금리 |
| ktb_3y | 817Y002 | 010200000 | 국고채(3년) |
| ktb_5y | 817Y002 | 010200001 | 국고채(5년) |
| ktb_10y | 817Y002 | 010210000 | 국고채(10년) |
| ktb_30y | 817Y002 | 010230000 | 국고채(30년) |
| corp_aa_minus_3y | 817Y002 | 010300000 | 회사채(3년, AA-) |
| corp_bbb_minus_3y | 817Y002 | 010320000 | 회사채(3년, BBB-) |

기준금리(722Y001)와 나머지 시장금리(817Y002)는 서로 다른 통계표라는 점에 주의.
기준금리는 발표 시차 때문에 시장금리보다 하루 늦게 갱신되는 날이 있을 수 있고,
그런 날에는 해당 값이 `None`으로 반환된다 (과거 값을 대신 채우지 않음).

## 뉴스 소스

네이버 뉴스 검색 API는 키가 없어 바로 RSS로 전환했다 (전부 무키, 공개 RSS):

- `get_market_events(date)`: 한국은행 보도자료(통화정책), 금융통화위원회 의결사항
- `search_news(query, days)`: 연합뉴스 경제, 한국경제 (제목+요약만, 본문 전체는 가져오지 않음)

## 회사채 수요예측 (DART)

`bond_agent/tools/dart.py`가 OpenDART API로 회사채 증권신고서(채무증권) 및
정정신고서를 찾고 원문을 받는다. **DART_API_KEY로 실제 공시를 조회해서 아래
두 가지 문제를 발견하고 고쳤다:**

- `list_bond_filings(start, end)`: `list.json`, `pblntf_detail_ty=C002`(증권신고-채무).
  실제로 조회해보니 C002에는 일반 회사채 증권신고서뿐 아니라 증권사의 ELS/DLS
  발행실적보고서·투자설명서도 함께 잡혀서(2026-08-01~09-25 기준 2,736건, 28페이지)
  ① 전체 페이지를 페이지네이션으로 다 가져오고 ② `report_nm`에 "증권신고서"가
  실제로 들어간 것만 남기도록 고쳤다 (정정신고서는 별도 코드가 없어 `report_nm`의
  "정정"으로 판별).
- `fetch_filing_text(rcept_no)`: `document.xml`로 원문 zip을 받아 텍스트로 풀고
  수요예측 "결과" 섹션을 잘라낸다. 처음엔 "수요예측" 첫 등장 지점을 썼는데, 실제
  공시(에스케이지오센트릭 [기재정정]증권신고서)로 확인해보니 그 지점이 정정사항
  "목록"의 제목일 뿐 실제 경쟁률/참여금액 표가 아니었다. 그래서 지금은
  ① "경쟁률" 등장 지점을 우선 쓰고 ② 그중에서도 "수요예측결과" 표제가 앞에
  있는 것을 우선한다 (SK 증권신고서에서 "경쟁률"이 자기 회사 결과가 아니라
  "동일등급 최근 발행내역" 비교표에도 나오는 걸 발견해서 추가한 안전장치).
  이 지점들을 실제 대한전선/에스케이지오센트릭/SK 공시로 확인했다.
  **그래도 완벽한 섹션 경계 탐지는 아닌 휴리스틱**이므로, 최종 확인은
  `scripts/review_bond_extractions.py`로 원문과 추출 결과를 나란히 보고 해야 한다.

`agent/extract.py`가 그 섹션을 LLM으로 구조화 추출한다 (발행사/신용등급/만기/
모집금액/참여금액/경쟁률/공모희망금리밴드/확정 가산금리/증액여부/최종발행금액).
`tool_choice`로 `record_bond_demand_extraction` 툴 호출을 강제해서 JSON Schema를
지키게 하고, 원문에 없는 값은 null로 두도록 프롬프트에서 지시한다. 추출 후
`_validate()`가 코드로 한 번 더 대조한다: 경쟁률 ≈ 참여금액/모집금액,
확정 가산금리가 공모희망금리 밴드 안에 있는지. 불일치는 `_validation.flags`에 담긴다.

알려진 한계: 한 공시에 2개 만기(예: 26-1회/26-2회)가 같이 나오면 추출 스키마가
하나의 결과만 담을 수 있어서 모델이 둘 중 하나만 고르거나 뭉뚱그릴 수 있다
(다중 회차 지원은 다음 단계).

**현재 상태: DART_API_KEY로 목록 조회/원문 다운로드/섹션 추출까지는 실제 공시로
검증했다.** 아직 ANTHROPIC_API_KEY가 없어서 `agent/extract.py`의 LLM 구조화
추출 자체는 가짜 데이터(`tests/test_extract.py`)로만 검증했다 - 키가 생기면
`scripts/review_bond_extractions.py`로 실제 공시 원문+추출 결과를 나란히 띄워서
확인하면 된다.

## Agent 레이어 (anthropic SDK 직접 구현, MockLLMBackend로 API 키 없이 테스트 가능)

- `agent/tools_schema.py`가 정의한 5개 툴: `get_market_snapshot`, `get_anomalies`,
  `get_us_yields`, `search_news`, `get_bond_demand_forecasts`
- `agent/loop.py`가 `LLMBackend.create_message(..., tools=TOOLS)`를 호출하고,
  응답이 `tool_use`면 `dispatch()`로 실행해서 `tool_result`로 돌려주는 것을
  최대 10회까지 반복한다. 매 호출/결과를 `logs/YYYY-MM-DD.jsonl`에 기록.
- 기본값(`LLM_BACKEND=mock`)에서는 `MockLLMBackend`가
  get_market_snapshot → get_anomalies → search_news → 최종 텍스트 순서의
  스크립트를 재생한다 (실제 ECOS/FRED/뉴스는 진짜로 호출됨). 시연용 최종
  텍스트에는 일부러 틀린 숫자("국고10년 100.0%")를 심어 놨는데, 실제로
  `scripts/run_briefing.py`를 돌리면 `agent/verify.py`가 이걸 잡아서 리포트
  하단에 경고 섹션을 붙이는 것까지 확인했다.
- 시스템 프롬프트(`agent/prompts.py`)는 get_market_snapshot/get_anomalies를
  먼저 호출할 것, 이상치가 있으면 search_news로 원인 후보를 찾을 것(못 찾으면
  "원인 불명확"), get_bond_demand_forecasts 검증 실패 항목은 "검증 필요"로
  표시할 것, 숫자는 툴 결과만 인용할 것을 명시한다. 출력은 6개 섹션(한 줄
  요약/금리 동향/스프레드/특이사항/체크포인트/크레딧 발행시장) 고정 형식.
- `agent/verify.py`가 최종 리포트에서 %/bp 숫자를 정규식으로 뽑아 이번 실행의
  툴 결과에 실제로 있었는지 대조하고, 불일치가 있으면 리포트 하단에 경고
  섹션을 붙인다.

## 실행

```bash
# 최근 영업일 기준 브리핑 데이터 표 출력 (숫자만, LLM 호출 없음, 비용 없음)
python scripts/demo_today.py
python scripts/demo_today.py 2026-09-23   # 특정 날짜 지정

# 에이전트 루프 실행 -> reports/YYYY-MM-DD.md 생성
python scripts/run_briefing.py                  # LLM_BACKEND=mock(기본값): 비용 없음, 시연용
python scripts/run_briefing.py 2026-09-23
LLM_BACKEND=anthropic python scripts/run_briefing.py 2026-09-23   # 실제 모델 호출, 비용 발생

# 최근 회사채 수요예측 공시 원문+추출결과 나란히 보기 (DART_API_KEY 필요)
python scripts/review_bond_extractions.py

# 테스트 (ECOS/FRED/뉴스 스모크 테스트는 .env에 키가 있어야 실행됨.
# test_dart.py / test_tools_schema.py / test_loop.py / test_extract.py / test_verify.py
# 는 모델·DART API를 호출하지 않아 비용 없음)
python -m pytest tests/ -v
```

## 다음 단계

- ANTHROPIC_API_KEY로 `LLM_BACKEND=anthropic` 실제 실행 검증, 이후 최근 3영업일
  리포트 생성 + 로그의 툴 호출 순서 요약
- ANTHROPIC_API_KEY로 `scripts/review_bond_extractions.py` 실제 LLM 추출 검증
  (dart.py의 목록조회/원문추출/섹션탐지는 이미 실제 공시로 검증 완료)
- 여러 만기(회차)가 한 공시에 같이 나오는 경우 다중 결과 추출 지원
- 뉴스 소스 확장 (필요하면 다른 매체/네이버 뉴스 검색 API 추가)
- 모닝 코멘트를 특정 포맷(예: 슬랙, 이메일, 사내 문서)으로 발송하는 레이어
- 스케줄러 연동 (매 영업일 아침 자동 실행)
