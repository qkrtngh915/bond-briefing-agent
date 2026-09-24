# 데일리 채권시장 브리핑 에이전트

국내 자산운용사 채권운용본부용 모닝 브리핑을 매 영업일 아침 자동 생성하는
에이전트. 숫자는 전부 코드로 계산하고, LLM은 "어떤 조사를 할지 판단"과
"서술"만 담당한다.

**샘플 리포트**: [이상치가 있던 날 (2026-09-11)](reports/2026-09-11.md) ·
[조용한 날 (2026-09-21)](reports/2026-09-21.md)

## 왜 만들었나

채권운용역의 아침 루틴은 대체로 똑같다 - 밤새 국고채/크레딧 금리가 얼마나
움직였는지 확인하고, 특이한 움직임이 있으면 원인이 될 만한 뉴스를 찾고,
최근 회사채 발행이 있었으면 수요예측 결과를 훑어본다. 이 작업 자체는
반복적이고 데이터 소스가 정해져 있는데, "오늘은 뭘 봐야 하는지"를 매번
사람이 판단해야 해서 완전히 자동화하기는 애매했다. 그래서 데이터 조회와
계산은 코드로 고정하고, "오늘 상황에서 무엇을 더 조사할지"는 에이전트가
판단하게 만들었다 - 조용한 날은 짧게, 이상치가 있는 날은 그 지표에 맞는
조사를 더 하도록.

## 아키텍처

```mermaid
flowchart LR
    subgraph tools["데이터 툴 (bond_agent/tools)"]
        ECOS["ecos.py<br/>한국 국고채/회사채"]
        FRED["fred.py<br/>미국 국채"]
        NEWS["news.py<br/>뉴스 RSS"]
        DART["dart.py<br/>회사채 공시 원문"]
    end

    subgraph calc["계산 레이어 (bond_agent/analytics)"]
        CURVE["curve.py<br/>전일 대비 변동 · 스프레드 · z-score"]
    end

    subgraph agentloop["에이전트 루프 (agent/)"]
        SCHEMA["tools_schema.py<br/>5개 tool 정의 + dispatch"]
        EXTRACT["extract.py<br/>수요예측 구조화 추출(LLM)"]
        LOOP["loop.py<br/>tool-use 루프 (최대 10회)"]
        BACKEND["llm_backend.py<br/>Mock ↔ Anthropic 전환"]
    end

    VERIFY["verify.py<br/>%/bp 숫자를 툴 결과와 대조"]
    REPORT["reports/YYYY-MM-DD.md"]
    LOGS[("logs/YYYY-MM-DD.jsonl<br/>모든 호출/응답 기록")]

    ECOS --> CURVE
    FRED --> CURVE
    CURVE --> SCHEMA
    NEWS --> SCHEMA
    DART --> EXTRACT --> SCHEMA
    BACKEND <--> LOOP
    SCHEMA --> LOOP
    LOOP --> VERIFY --> REPORT
    LOOP -.-> LOGS
```

## 설계 포인트

- **숫자는 코드, 해석은 LLM.** `curve.py`가 변동(bp)·스프레드·z-score를 전부
  계산해서 넘기고, LLM은 그 값을 인용/서술만 한다. 프롬프트에도 "스스로
  계산/추정하지 말 것"을 명시했지만, 실제로 미국 금리 변동을 스스로 계산해서
  쓴 사례가 있었다 (아래 "실전 검증" 참고) - 프롬프트만으로는 완벽히 막을 수
  없어서 검증 레이어가 필요했다.
- **verify.py가 마지막 안전망.** 리포트 텍스트에서 %/bp 숫자를 정규식으로
  뽑아 이번 실행에서 실제로 호출한 툴 결과(숫자형 필드는 물론, 뉴스 제목
  같은 문자열 안에 박힌 숫자까지)와 대조한다. 불일치하면 리포트 하단에
  경고 섹션을 붙인다 - 모델을 막지는 못해도, 사람이 검증 없이 그대로 믿고
  넘어가는 걸 막는다.
- **z-score 기반 조건부 추가 조사.** 필수 툴은 `get_market_snapshot`,
  `get_anomalies` 둘뿐이다. `get_anomalies`가 |z|>2인 지표를 하나도 못 찾으면
  뉴스 검색 없이 짧게 끝나고, 이상치가 있으면 그 지표 종류(국고채 커브 /
  크레딧 스프레드 / 한미 금리차)에 맞는 검색어로 `search_news`를 호출한다.
  자세한 비교는 [docs/agent_decision_comparison.md](docs/agent_decision_comparison.md).
- **전 과정 로그.** 모든 tool_call/tool_result/model_response가
  `logs/YYYY-MM-DD.jsonl`에 한 줄씩 남는다 - 에이전트가 어떤 순서로 어떤
  판단을 했는지 나중에 그대로 재구성할 수 있다.

## 에이전트 판단 과정 예시

이상치가 있는 날(2026-09-11)과 없는 날(2026-09-21)에 실제로 다른 경로를
타는지 실행해서 비교했다:

| | 이상치 있는 날 | 이상치 없는 날 |
|---|---|---|
| 호출한 툴 | market_snapshot → anomalies → search_news×2(지표별 검색어) → bond_demand_forecasts | market_snapshot → anomalies → bond_demand_forecasts |
| 턴 수 | 4 | 3 |
| 리포트 | 이상치 3개 + 원인 후보 뉴스 포함, 길다 | "특이사항 없음" 한 줄, 짧다 |

지표 종류에 따라 검색어도 실제로 달라졌다: 국고채 커브 이상치 →
"국고채 금리", 크레딧 스프레드 이상치 → "회사채 크레딧 스프레드". 자세한
내용과 실행 중 발견한 문제는 [docs/agent_decision_comparison.md](docs/agent_decision_comparison.md) 참고.

## 실전 검증에서 잡은 버그

전부 실제 API/실제 공시/실제 모델로 돌려보다가 발견해서 코드나 프롬프트로
고친 것들이다 (가짜 데이터 단위 테스트로는 못 잡았을 종류의 문제들):

| 문제 | 원인 | 해결 |
|---|---|---|
| DART 공시 목록에 회사채가 아닌 것들이 대량 섞여 나옴 | `pblntf_detail_ty=C002`가 증권사 ELS/DLS 발행실적보고서·투자설명서까지 포함 (56일치 2,736건) | 전체 페이지네이션 후 `report_nm`에 "증권신고서"가 실제로 있는 것만 필터링 |
| 수요예측 결과 섹션 추출이 엉뚱한 곳을 집음 | "수요예측" 첫 등장 지점이 실제로는 정정사항 "목록"의 제목이었음 | "경쟁률" 등장 지점을 우선하고, 그중 "수요예측결과" 표제가 앞에 있는 것을 우선 (비교 발행내역 표의 "경쟁률"과 구분) |
| 검증 레이어가 정상적으로 인용된 숫자를 오탐 | 뉴스 헤드라인의 숫자("5.44%")가 문자열 필드에 있어서, 숫자형 필드만 훑는 대조 로직이 못 찾음 | 문자열 값 안에 박힌 %/bp 숫자도 대조 대상에 포함 |
| 모델이 미국 금리 변동을 스스로 계산해서 씀 | `get_us_yields`는 레벨만 주고 전일 대비 변동을 제공하는 툴이 없어서, 모델이 두 레벨을 직접 빼서 "-5bp"를 만들어냄 | 프롬프트에 "미국 금리는 레벨로만 언급, 변동폭 직접 계산 금지" 명시 |
| 다중 회차 공시에서 모집금액이 10배 축소됨 | "이백일십억원(₩21,000,000,000)"을 210억이 아니라 21억으로 환산 | 프롬프트에 "괄호 안 원화 숫자를 1억으로 나눠서 환산" 규칙 추가 |
| (선택 툴로 분류된) `get_bond_demand_forecasts`를 호출 안 하고 "최근 발행 없음"이라 근거 없이 씀 | 이상치가 없는 날 모델이 이 툴을 완전히 스킵 - 숫자가 아니라 verify.py도 못 잡는 환각 | 이 툴은 이상치 유무와 무관하게 섹션 6 작성 시 항상 호출하도록 재분류 |

## 한계

- **원문 섹션 탐지는 완벽한 파서가 아니라 휴리스틱이다.** 회사/공시 서식이
  다양해서 새로운 형태의 공시에서 또 틀릴 수 있다.
- **다중 회차 추출은 한 공시 안에서만 분리한다.** 서로 다른 공시에 걸쳐
  같은 회사의 같은 채권이 여러 번 언급되는 경우(정정 → 발행조건확정)를
  하나로 묶어주지는 않는다.
- **한글 숫자/금액 환산은 여전히 모델이 읽는 것에 의존한다.** 이번에 한
  가지 패턴(괄호 안 원화 숫자)의 오류는 고쳤지만, 다른 표기 방식에서 같은
  종류의 오류가 또 날 수 있다 - 숫자는 항상 원문과 대조해야 한다.
- **verify.py는 %/bp가 붙은 숫자만 검사한다.** z-score를 단위 없이 쓰거나,
  "최근 발행 없음"처럼 숫자가 아닌 근거 없는 주장은 못 잡는다 (위 표의
  마지막 항목처럼, 이런 건 프롬프트로 막아야 한다).
- **뉴스 검색은 연합뉴스/한국경제 RSS 최근 항목에 한정.** 오래된 과거
  날짜를 조회하면 그 시점 뉴스가 이미 피드에서 밀려나 빈 결과가 나올 수 있다.
- **GitHub Actions 워크플로는 실제로 실행해보지 않았다.** 로컬에서 YAML
  문법과 구조, 크론의 UTC↔KST 매핑만 검증했다 (원격 저장소가 없어서 실제
  Actions 실행 자체는 못 함).

## 설치 및 실행

```bash
python -m pip install -r requirements.txt
cp .env.example .env   # ECOS_API_KEY, FRED_API_KEY, DART_API_KEY, ANTHROPIC_API_KEY 채우기

# 숫자만 표로 확인 (LLM 호출 없음, 비용 없음)
python scripts/demo_today.py

# 에이전트 루프 실행 -> reports/YYYY-MM-DD.md 생성
python scripts/run_briefing.py                                    # LLM_BACKEND=mock(기본값), 비용 없음
LLM_BACKEND=anthropic python scripts/run_briefing.py 2026-09-23    # 실제 모델 호출, 비용 발생

# 최근 회사채 수요예측 공시 원문+추출결과 나란히 보기 (DART_API_KEY 필요)
python scripts/review_bond_extractions.py

# 테스트 (ECOS/FRED/뉴스 스모크 테스트는 .env 키 필요. 나머지는 Mock/가짜 데이터라 비용 없음)
python -m pytest tests/ -v
```

`.env`의 `ECOS_API_KEY`/`FRED_API_KEY`/`DART_API_KEY`는 각각
[ecos.bok.or.kr](https://ecos.bok.or.kr) · [FRED](https://fred.stlouisfed.org/docs/api/api_key.html) ·
[opendart.fss.or.kr](https://opendart.fss.or.kr) 에서 무료로 발급받는다.
`ANTHROPIC_API_KEY`는 [console.anthropic.com](https://console.anthropic.com)에서
발급받고, `LLM_BACKEND=anthropic`일 때만 필요하다 (비용 발생).

### 폴더 구조

```
bond_agent/
  tools/         ecos.py, fred.py, news.py, dart.py, calendar.py, _cache.py
  analytics/     curve.py (변동/스프레드/이상치 계산)
  config.py      경로, API 키, ECOS 코드, LLM_BACKEND
agent/
  prompts.py, llm_backend.py, tools_schema.py, loop.py, extract.py, verify.py
scripts/         demo_today.py, run_briefing.py, review_bond_extractions.py,
                 check_business_day.py, lookup_ecos_codes.py, generate_extract_check.py
tests/           각 모듈별 단위/스모크 테스트
docs/            extract_check.md, agent_decision_comparison.md (검증 근거)
.github/workflows/daily_briefing.yml   매 평일 07:30 KST 자동 실행
data/cache/, logs/, reports/
```

## 로드맵

앞으로의 확장 방향은 [ROADMAP.md](ROADMAP.md)에 정리했다.
