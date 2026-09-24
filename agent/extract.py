"""증권신고서(채무증권) 원문에서 수요예측 관련 구조화 데이터를 LLM으로 추출.

JSON Schema를 강제하는 tool 하나만 정의해서, tool_choice로 모델이 그 tool을
반드시 호출하도록 강제한다 (Anthropic Messages API의 구조화 출력 패턴).
텍스트에 명시적으로 없는 값은 null로 남기도록 시스템 프롬프트에서 지시하고,
추출 후에는 경쟁률/가산금리/발행금액 비율 같은 코드 검증을 회차(tranche)별로
한 번 더 거친다.

한 공시에 여러 만기(회차, 예: 26-1회/26-2회)가 함께 실리는 경우가 실제로
많다는 걸 실제 공시로 확인했다 - 그래서 issuer/credit_rating은 공시 전체에
공통이라고 보고 한 번만, 나머지(만기/모집금액/경쟁률/밴드/확정가산금리/증액/
최종발행금액)는 tranches 배열로 회차마다 따로 추출한다.

금액 단위(억원/백만원/원) 정규화는 LLM이 아니라 코드(_to_eok_won)에서 한다.
LLM에게 억원 환산까지 시켰다가 실제로 "이백일십억원(₩21,000,000,000)"을
21억으로 잘못 환산한 사례가 있었다 (10배 축소) - 그래서 LLM은 원문에 있는
숫자와 단위 문자열을 그대로만 추출하고, 환산은 코드가 결정론적으로 한다.

LLM 백엔드는 agent.llm_backend.LLMBackend 인터페이스를 그대로 재사용한다
(Mock/Anthropic 전환 가능, agent/loop.py와 동일한 방식).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.llm_backend import LLMBackend, ToolUseBlock
from bond_agent.config import BASE_DIR

_EXTRACT_TOOL_NAME = "record_bond_demand_extraction"

# 공시(rcept_no)별 추출 결과 영구 캐시. DART 원문(data/cache/dart/)과 달리,
# 이건 "LLM이 그 원문에서 뽑아낸 결과"를 캐시하는 것 - 같은 공시를 다시 만나면
# 실제 LLM 호출(비용) 없이 이 캐시를 그대로 쓴다. EXTRACT_PROMPT_VERSION이
# 바뀌면(추출 스키마/프롬프트가 바뀌면) 캐시를 무효화하고 재추출한다.
EXTRACTED_CACHE_DIR = BASE_DIR / "data" / "extracted"
EXTRACTED_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# EXTRACTION_TOOL(입력 스키마) 또는 _EXTRACT_SYSTEM_PROMPT가 바뀔 때마다 올린다.
# data/extracted/ 캐시가 이 값을 저장해두고, 값이 바뀌면 캐시를 무효화하고
# 재추출한다 (agent/tools_schema.py의 get_bond_demand_forecasts 참고).
EXTRACT_PROMPT_VERSION = "1"

_UNIT_TO_EOK_WON: dict[str, float] = {
    "억원": 1.0,
    "백만원": 0.01,
    "만원": 0.0001,
    "원": 1e-8,
}


def _amount_schema(korean_label: str) -> dict[str, Any]:
    return {
        "type": ["object", "null"],
        "description": f"{korean_label}. 절대 억원으로 환산하지 말고, 원문에 나온 숫자와 단위를 그대로 적으세요 (단위 변환은 코드가 함).",
        "properties": {
            "value": {
                "type": ["number", "null"],
                "description": "원문에 표기된 숫자 그대로 (환산 금지)",
            },
            "unit": {
                "type": ["string", "null"],
                "description": (
                    "원문에 표기된 단위 그대로: '억원', '백만원', '원' 중 하나. "
                    "\"이백일십억원 (₩21,000,000,000)\"처럼 한글 표기와 괄호 안 "
                    "아라비아 숫자가 같이 나오면, 괄호 안의 정확한 숫자와 '원' "
                    "단위를 우선 사용하세요 (한글 숫자 읽기보다 오류가 적음)."
                ),
            },
        },
    }


_TRANCHE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "series_label": {
            "type": ["string", "null"],
            "description": "회차 식별자 (예: '제26-1회', '155-2', '8-1'). 회차 구분이 없는 단일 발행이면 null.",
        },
        "maturity": {"type": ["string", "null"], "description": "만기 (예: '3년')"},
        "initial_offering_amount": _amount_schema("이 회차의 최초 모집금액"),
        "demand_participation_amount": _amount_schema("이 회차의 수요예측 참여금액"),
        "competition_ratio": {
            "type": ["number", "null"],
            "description": "이 회차의 경쟁률 (배수, 예: 2.5)",
        },
        "coupon_guidance_band_bp": {
            "type": ["object", "null"],
            "description": "이 회차의 공모희망금리 밴드, 개별민평 대비 bp",
            "properties": {
                "low_bp": {"type": ["number", "null"]},
                "high_bp": {"type": ["number", "null"]},
            },
        },
        "final_spread_bp": {
            "type": ["number", "null"],
            "description": "이 회차의 확정 가산금리, 개별민평 대비 bp",
        },
        "upsized": {
            "type": ["boolean", "null"],
            "description": "이 회차가 당초 계획보다 증액 발행되었는지 여부",
        },
        "final_issue_amount": _amount_schema("이 회차의 최종 발행금액"),
    },
    "required": [
        "series_label",
        "maturity",
        "initial_offering_amount",
        "demand_participation_amount",
        "competition_ratio",
        "coupon_guidance_band_bp",
        "final_spread_bp",
        "upsized",
        "final_issue_amount",
    ],
}

EXTRACTION_TOOL: dict[str, Any] = {
    "name": _EXTRACT_TOOL_NAME,
    "description": (
        "증권신고서(채무증권) 원문에서 확인한 값만 기록한다. 원문에 명시적으로 없는 "
        "값은 반드시 null로 남긴다 (추정 금지). 금액은 절대 환산하지 말고 원문 숫자와 "
        "단위를 그대로 기록한다 (환산은 코드가 함). 한 공시에 여러 회차(만기)가 있으면 "
        "tranches 배열에 회차마다 하나씩 항목을 만든다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "issuer": {"type": ["string", "null"], "description": "발행사명 (공시 전체 공통)"},
            "credit_rating": {
                "type": ["string", "null"],
                "description": "신용등급 (예: AA-, BBB+). 회차별로 다르면 가장 먼저 언급된 값 하나만.",
            },
            "tranches": {
                "type": "array",
                "description": "회차별 결과 목록. 회차 구분이 없는 단일 발행이면 배열에 항목 1개만.",
                "items": _TRANCHE_SCHEMA,
            },
        },
        "required": ["issuer", "credit_rating", "tranches"],
    },
}

_EXTRACT_SYSTEM_PROMPT = """당신은 채권 발행 공시(증권신고서-채무증권) 원문에서 수요예측 결과를 정확히 추출하는 보조입니다.

규칙:
- record_bond_demand_extraction 툴을 정확히 한 번 호출해서 결과를 기록하세요.
- 원문에 명시적으로 나온 값만 채우세요. 추정하거나 계산해서 채우지 마세요.
- 원문에 없는 항목은 반드시 null로 남기세요.
- 한 공시에 여러 회차(예: 26-1회, 26-2회처럼 만기가 다른 여러 채권)가 함께
  실려 있으면, 절대 하나로 뭉뚱그리지 말고 tranches 배열에 회차마다 별도
  항목을 만드세요. 회차가 하나뿐이면 배열에 항목을 1개만 넣으세요.
- 금액(모집금액/참여금액/최종발행금액)은 절대 억원으로 스스로 환산하지
  마세요. value에는 원문 숫자를, unit에는 원문 그대로의 단위("억원",
  "백만원", "원" 중 하나)를 넣으세요. 단위 환산은 당신이 아니라 코드가
  합니다 - 당신이 환산하면 자릿수를 놓치는 사고가 실제로 있었습니다.
  "이백일십억원 (₩21,000,000,000)"처럼 한글 표기와 괄호 안 아라비아 숫자가
  같이 나오면, 괄호 안의 정확한 숫자(21000000000)와 단위 "원"을 쓰세요
  (한글 숫자를 직접 읽는 것보다 오류가 적습니다).
"""


def _to_eok_won(amount: dict[str, Any] | None) -> float | None:
    """LLM이 추출한 {"value": .., "unit": ..} 원시 금액을 억원 단위 float로 변환.

    단위 정규화는 여기(코드)에서만 한다 - 모듈 docstring에 적은 실제 사고
    (이백일십억원을 21억으로 잘못 환산) 때문에 LLM에게 맡기지 않는다.
    """
    if not amount:
        return None
    value = amount.get("value")
    unit = amount.get("unit")
    if value is None or unit not in _UNIT_TO_EOK_WON:
        return None
    return round(value * _UNIT_TO_EOK_WON[unit], 4)


def extract_filing(text: str, backend: LLMBackend) -> dict[str, Any]:
    """원문 텍스트(주로 dart.fetch_filing_text의 demand_forecast_section)에서
    회차별 구조화 데이터를 추출하고, 금액은 코드로 억원 환산 + 회차마다 코드
    레벨 검증을 붙여서 반환한다.

    Args:
        text: 추출 대상 원문 (수요예측 관련 섹션).
        backend: agent.llm_backend.LLMBackend 구현체 (Mock 또는 Anthropic).

    Returns:
        {
          "issuer": str | None,
          "credit_rating": str | None,
          "tranches": [
            {
              "series_label": ..., "maturity": ...,
              "initial_offering_amount_billion_won": float | None,  # 코드가 환산
              "initial_offering_amount_raw": {"value":.., "unit":..} | None,  # LLM 원본
              "demand_participation_amount_billion_won": float | None,
              "demand_participation_amount_raw": {...} | None,
              "competition_ratio": ..., "coupon_guidance_band_bp": ...,
              "final_spread_bp": ..., "upsized": ...,
              "final_issue_amount_billion_won": float | None,
              "final_issue_amount_raw": {...} | None,
              "_validation": {"flags": [str, ...]},
            },
            ...
          ],
        }
    """
    messages = [
        {
            "role": "user",
            "content": f"다음은 증권신고서(채무증권) 원문 중 수요예측 관련 부분입니다:\n\n{text}",
        }
    ]
    response = backend.create_message(
        system=_EXTRACT_SYSTEM_PROMPT,
        messages=messages,
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": _EXTRACT_TOOL_NAME},
    )

    tool_call = next((b for b in response.content if isinstance(b, ToolUseBlock)), None)
    if tool_call is None:
        raise RuntimeError("LLM이 record_bond_demand_extraction 툴을 호출하지 않았습니다.")

    raw = dict(tool_call.input)
    tranches = raw.get("tranches") or []
    validated_tranches = []
    for tranche in tranches:
        validated_tranches.append(_finalize_tranche(dict(tranche)))

    return {
        "issuer": raw.get("issuer"),
        "credit_rating": raw.get("credit_rating"),
        "tranches": validated_tranches,
    }


def _cache_path(rcept_no: str) -> Path:
    return EXTRACTED_CACHE_DIR / f"{rcept_no}.json"


def load_cached_extraction(rcept_no: str) -> dict[str, Any] | None:
    """rcept_no의 캐시된 추출 결과를 읽는다.

    캐시 파일이 없거나, 저장된 extract_prompt_version이 지금
    EXTRACT_PROMPT_VERSION과 다르면(추출 스키마/프롬프트가 바뀌었으면) None을
    반환해서 재추출을 유도한다.
    """
    path = _cache_path(rcept_no)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if payload.get("extract_prompt_version") != EXTRACT_PROMPT_VERSION:
        return None
    return payload.get("result")


def save_cached_extraction(rcept_no: str, result: dict[str, Any]) -> None:
    """추출 결과를 data/extracted/{rcept_no}.json 에 영구 저장한다."""
    payload = {
        "rcept_no": rcept_no,
        "extract_prompt_version": EXTRACT_PROMPT_VERSION,
        "result": result,
    }
    _cache_path(rcept_no).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def extract_filing_cached(rcept_no: str, text: str, backend: LLMBackend) -> dict[str, Any]:
    """extract_filing()을 rcept_no별 영구 캐시로 감싼다.

    캐시에 있고 프롬프트 버전이 일치하면 실제 LLM을 전혀 호출하지 않고 그
    결과를 그대로 반환한다. 없으면 extract_filing()을 호출하고 결과를
    캐시에 저장한 뒤 반환한다.
    """
    cached = load_cached_extraction(rcept_no)
    if cached is not None:
        return cached
    result = extract_filing(text, backend)
    save_cached_extraction(rcept_no, result)
    return result


def _finalize_tranche(tranche: dict[str, Any]) -> dict[str, Any]:
    """LLM이 준 raw 금액(value+unit)을 코드로 억원 환산해서 채워 넣고, 검증까지 붙인다."""
    offering_raw = tranche.pop("initial_offering_amount", None)
    participation_raw = tranche.pop("demand_participation_amount", None)
    final_issue_raw = tranche.pop("final_issue_amount", None)

    tranche["initial_offering_amount_raw"] = offering_raw
    tranche["initial_offering_amount_billion_won"] = _to_eok_won(offering_raw)
    tranche["demand_participation_amount_raw"] = participation_raw
    tranche["demand_participation_amount_billion_won"] = _to_eok_won(participation_raw)
    tranche["final_issue_amount_raw"] = final_issue_raw
    tranche["final_issue_amount_billion_won"] = _to_eok_won(final_issue_raw)

    tranche["_validation"] = _validate_tranche(tranche)
    return tranche


def _validate_tranche(tranche: dict[str, Any]) -> dict[str, Any]:
    """회차 하나에 대해 코드로 대조:
    - 경쟁률 ≈ 참여금액/모집금액
    - 확정 가산금리가 공모희망금리 밴드 안에 있는지
    - 최종발행금액/모집금액 비율이 상식적인 범위(0.5~2.5배) 안에 있는지

    Returns:
        {"flags": [str, ...]} - 문제가 있으면 사람이 읽을 수 있는 설명들, 없으면 빈 리스트.
    """
    flags: list[str] = []
    label = tranche.get("series_label") or "(회차 미지정)"

    participation = tranche.get("demand_participation_amount_billion_won")
    offering = tranche.get("initial_offering_amount_billion_won")
    competition = tranche.get("competition_ratio")
    if participation is not None and offering is not None and offering > 0 and competition is not None:
        implied = participation / offering
        tolerance = 0.05 * max(competition, 1)
        if abs(implied - competition) > tolerance:
            flags.append(
                f"[{label}] 경쟁률 불일치: 명시된 경쟁률={competition}, "
                f"참여금액/모집금액={implied:.2f} (참여금액={participation}억, 모집금액={offering}억)"
            )

    band = tranche.get("coupon_guidance_band_bp")
    final_spread = tranche.get("final_spread_bp")
    if band and final_spread is not None:
        low, high = band.get("low_bp"), band.get("high_bp")
        if low is not None and high is not None and not (low <= final_spread <= high):
            flags.append(
                f"[{label}] 확정 가산금리({final_spread}bp)가 공모희망금리 밴드({low}~{high}bp) 밖입니다."
            )

    final_issue = tranche.get("final_issue_amount_billion_won")
    if final_issue is not None and offering is not None and offering > 0:
        ratio = final_issue / offering
        if not (0.5 <= ratio <= 2.5):
            flags.append(
                f"[{label}] 최종발행금액/모집금액 비율 비정상: {ratio:.2f}배 "
                f"(최종발행금액={final_issue}억, 모집금액={offering}억) - 상식적 범위(0.5~2.5배) 밖"
            )

    return {"flags": flags}
