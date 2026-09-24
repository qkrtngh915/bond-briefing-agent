"""증권신고서(채무증권) 원문에서 수요예측 관련 구조화 데이터를 LLM으로 추출.

JSON Schema를 강제하는 tool 하나만 정의해서, tool_choice로 모델이 그 tool을
반드시 호출하도록 강제한다 (Anthropic Messages API의 구조화 출력 패턴).
텍스트에 명시적으로 없는 값은 null로 남기도록 시스템 프롬프트에서 지시하고,
추출 후에는 경쟁률/가산금리를 코드로 한 번 더 대조해서 불일치를 flag한다.

LLM 백엔드는 agent.llm_backend.LLMBackend 인터페이스를 그대로 재사용한다
(Mock/Anthropic 전환 가능, agent/loop.py와 동일한 방식).
"""

from __future__ import annotations

from typing import Any

from agent.llm_backend import LLMBackend, ToolUseBlock

_EXTRACT_TOOL_NAME = "record_bond_demand_extraction"

EXTRACTION_TOOL: dict[str, Any] = {
    "name": _EXTRACT_TOOL_NAME,
    "description": (
        "증권신고서(채무증권) 원문에서 확인한 값만 기록한다. 원문에 명시적으로 없는 "
        "값은 반드시 null로 남긴다 (추정 금지)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "issuer": {"type": ["string", "null"], "description": "발행사명"},
            "credit_rating": {"type": ["string", "null"], "description": "신용등급 (예: AA-, BBB+)"},
            "maturity": {"type": ["string", "null"], "description": "만기 (예: '3년')"},
            "initial_offering_amount_billion_won": {
                "type": ["number", "null"],
                "description": "최초 모집금액 (억원 단위)",
            },
            "demand_participation_amount_billion_won": {
                "type": ["number", "null"],
                "description": "수요예측 참여금액 (억원 단위)",
            },
            "competition_ratio": {
                "type": ["number", "null"],
                "description": "경쟁률 (배수, 예: 2.5)",
            },
            "coupon_guidance_band_bp": {
                "type": ["object", "null"],
                "description": "공모희망금리 밴드, 개별민평 대비 bp",
                "properties": {
                    "low_bp": {"type": ["number", "null"]},
                    "high_bp": {"type": ["number", "null"]},
                },
            },
            "final_spread_bp": {
                "type": ["number", "null"],
                "description": "확정 가산금리, 개별민평 대비 bp",
            },
            "upsized": {
                "type": ["boolean", "null"],
                "description": "당초 계획보다 증액 발행했는지 여부",
            },
            "final_issue_amount_billion_won": {
                "type": ["number", "null"],
                "description": "최종 발행금액 (억원 단위)",
            },
        },
        "required": [
            "issuer",
            "credit_rating",
            "maturity",
            "initial_offering_amount_billion_won",
            "demand_participation_amount_billion_won",
            "competition_ratio",
            "coupon_guidance_band_bp",
            "final_spread_bp",
            "upsized",
            "final_issue_amount_billion_won",
        ],
    },
}

_EXTRACT_SYSTEM_PROMPT = """당신은 채권 발행 공시(증권신고서-채무증권) 원문에서 수요예측 결과를 정확히 추출하는 보조입니다.

규칙:
- record_bond_demand_extraction 툴을 정확히 한 번 호출해서 결과를 기록하세요.
- 원문에 명시적으로 나온 값만 채우세요. 추정하거나 계산해서 채우지 마세요.
- 원문에 없는 항목은 반드시 null로 남기세요.
"""


def extract_filing(text: str, backend: LLMBackend) -> dict[str, Any]:
    """원문 텍스트(주로 dart.fetch_filing_text의 demand_forecast_section)에서
    구조화 데이터를 추출하고, 코드 레벨 검증까지 붙여서 반환한다.

    Args:
        text: 추출 대상 원문 (수요예측 관련 섹션).
        backend: agent.llm_backend.LLMBackend 구현체 (Mock 또는 Anthropic).

    Returns:
        EXTRACTION_TOOL의 필드들 + "_validation": {"flags": [str, ...]}.
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

    extracted = dict(tool_call.input)
    extracted["_validation"] = _validate(extracted)
    return extracted


def _validate(extracted: dict[str, Any]) -> dict[str, Any]:
    """경쟁률 ≈ 참여금액/모집금액, 확정 가산금리가 밴드 안에 있는지 코드로 대조.

    Returns:
        {"flags": [str, ...]} - 문제가 있으면 사람이 읽을 수 있는 설명들, 없으면 빈 리스트.
    """
    flags: list[str] = []

    participation = extracted.get("demand_participation_amount_billion_won")
    offering = extracted.get("initial_offering_amount_billion_won")
    competition = extracted.get("competition_ratio")
    if participation is not None and offering is not None and offering > 0 and competition is not None:
        implied = participation / offering
        tolerance = 0.05 * max(competition, 1)
        if abs(implied - competition) > tolerance:
            flags.append(
                f"경쟁률 불일치: 명시된 경쟁률={competition}, "
                f"참여금액/모집금액={implied:.2f} (참여금액={participation}억, 모집금액={offering}억)"
            )

    band = extracted.get("coupon_guidance_band_bp")
    final_spread = extracted.get("final_spread_bp")
    if band and final_spread is not None:
        low, high = band.get("low_bp"), band.get("high_bp")
        if low is not None and high is not None and not (low <= final_spread <= high):
            flags.append(
                f"확정 가산금리({final_spread}bp)가 공모희망금리 밴드({low}~{high}bp) 밖입니다."
            )

    return {"flags": flags}
