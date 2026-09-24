"""tool-use 루프 (anthropic SDK를 직접 감싼 AnthropicLLMBackend, 또는 API 키 없이
동작하는 MockLLMBackend 중 bond_agent.config.LLM_BACKEND로 선택).

흐름: 시스템 프롬프트 + "YYYY-MM-DD 모닝 브리핑 작성해줘" -> 모델이 tool_use를
반환하면 실행해서 tool_result로 돌려주고, end_turn(stop_reason != "tool_use")이
될 때까지 반복한다 (최대 MAX_ITERS회). 모든 툴 호출/결과를
logs/YYYY-MM-DD.jsonl 에 한 줄씩 기록한다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agent.llm_backend import (
    AnthropicLLMBackend,
    LLMBackend,
    MockLLMBackend,
    TextBlock,
    ToolUseBlock,
    build_demo_mock_script,
)
from agent.prompts import SYSTEM_PROMPT
from agent.tools_schema import TOOLS, dispatch
from agent.verify import annotate_with_warnings, collect_known_numbers, verify_report
from bond_agent.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, LLM_BACKEND, LOGS_DIR

MAX_ITERS = 10


def _log_path(date: str) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / f"{date}.jsonl"


def _append_log(date: str, event: dict[str, Any]) -> None:
    record = {"ts": time.time(), **event}
    with _log_path(date).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _block_to_log(block: Any) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "name": block.name, "input": block.input, "id": block.id}
    return {"type": "unknown"}


def _default_backend(date: str) -> LLMBackend:
    if LLM_BACKEND == "anthropic":
        if not ANTHROPIC_API_KEY:
            raise RuntimeError(
                "LLM_BACKEND=anthropic 인데 ANTHROPIC_API_KEY가 설정되지 않았습니다. "
                ".env 파일을 확인하세요."
            )
        return AnthropicLLMBackend(api_key=ANTHROPIC_API_KEY, model=ANTHROPIC_MODEL)
    return MockLLMBackend(build_demo_mock_script(date))


def run_agent_loop(date: str, backend: LLMBackend | None = None) -> dict[str, Any]:
    """지정한 날짜 기준 모닝 브리핑을 생성한다.

    Args:
        date: 기준일 "YYYY-MM-DD".
        backend: 명시하지 않으면 bond_agent.config.LLM_BACKEND 값으로 자동 선택
            (mock 또는 anthropic). 테스트에서 직접 주입할 수도 있다.

    Returns:
        {
          "date": date,
          "report_markdown": str,             # 검증 경고가 붙어 있을 수 있는 최종 리포트
          "tool_call_order": [str, ...],       # 이번 실행에서 호출된 툴 이름 순서
          "num_turns": int,
          "verification_mismatches": [dict],   # agent.verify 결과
          "backend": "mock" | "anthropic",
        }
    """
    backend = backend or _default_backend(date)
    backend_name = "anthropic" if isinstance(backend, AnthropicLLMBackend) else "mock"

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": f"{date} 기준으로 데일리 채권시장 모닝 브리핑을 작성해줘."}
    ]

    tool_call_order: list[str] = []
    all_tool_outputs: list[Any] = []
    final_text: str | None = None
    turn = 0

    _append_log(date, {"type": "run_start", "date": date, "backend": backend_name})

    for turn in range(1, MAX_ITERS + 1):
        response = backend.create_message(system=SYSTEM_PROMPT, messages=messages, tools=TOOLS)
        _append_log(
            date,
            {
                "type": "model_response",
                "turn": turn,
                "stop_reason": response.stop_reason,
                "content": [_block_to_log(b) for b in response.content],
            },
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            final_text = "".join(b.text for b in response.content if isinstance(b, TextBlock))
            break

        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            if not isinstance(block, ToolUseBlock):
                continue
            tool_call_order.append(block.name)
            _append_log(date, {"type": "tool_call", "turn": turn, "name": block.name, "input": block.input})
            try:
                output = dispatch(block.name, block.input, backend=backend)
                all_tool_outputs.append(output)
                _append_log(date, {"type": "tool_result", "turn": turn, "name": block.name, "output": output})
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(output, ensure_ascii=False),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - 에이전트에게 오류를 그대로 전달
                _append_log(date, {"type": "tool_error", "turn": turn, "name": block.name, "error": str(exc)})
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"오류: {exc}",
                        "is_error": True,
                    }
                )
        messages.append({"role": "user", "content": tool_results})
    else:
        final_text = "(최대 반복 횟수를 초과해서 브리핑을 완성하지 못했습니다.)"
        _append_log(date, {"type": "max_iters_exceeded", "max_iters": MAX_ITERS})

    known_values = collect_known_numbers(all_tool_outputs)
    mismatches = verify_report(final_text or "", known_values)
    report_markdown = annotate_with_warnings(final_text or "", mismatches)

    _append_log(
        date,
        {
            "type": "run_end",
            "tool_call_order": tool_call_order,
            "num_turns": turn,
            "verification_mismatches": mismatches,
        },
    )

    return {
        "date": date,
        "report_markdown": report_markdown,
        "tool_call_order": tool_call_order,
        "num_turns": turn,
        "verification_mismatches": mismatches,
        "backend": backend_name,
    }
