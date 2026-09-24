"""agent/loop.py 단위 테스트: MockLLMBackend로 실제 모델 호출 없이 루프/로그/검증 흐름을 검증한다.

dispatch()도 monkeypatch해서 실제 ECOS/FRED/뉴스 API를 호출하지 않는다
(빠르고 오프라인에서도 도는 순수 로직 테스트).
"""

from __future__ import annotations

import json

from agent import loop, tools_schema
from agent.llm_backend import MockLLMBackend, build_demo_mock_script


def test_run_agent_loop_with_mock_backend_calls_tools_in_scripted_order(monkeypatch, tmp_path):
    monkeypatch.setattr(loop, "LOGS_DIR", tmp_path)

    def fake_dispatch(name, tool_input, backend=None):
        if name == "get_market_snapshot":
            return {"daily_changes": {"levels": {"ktb_10y": 4.39}}, "spreads": {}}
        if name == "get_anomalies":
            return {"metrics": {"ktb_10y": {"z_score": -1.5, "is_anomaly": False}}}
        if name == "search_news":
            return [{"title": "테스트 기사", "source": "테스트", "published_at": "2026-09-23T09:00:00+09:00", "url": "https://example.com", "summary": ""}]
        raise AssertionError(f"예상치 못한 툴 호출: {name}")

    monkeypatch.setattr(loop, "dispatch", fake_dispatch)

    date = "2026-09-23"
    backend = MockLLMBackend(build_demo_mock_script(date))
    result = loop.run_agent_loop(date, backend=backend)

    assert result["backend"] == "mock"
    assert result["tool_call_order"] == ["get_market_snapshot", "get_anomalies", "search_news"]
    assert result["num_turns"] == 4  # 3번의 tool_use 턴 + 최종 텍스트 턴

    # 시연용 스크립트가 심어둔 가짜 숫자(100.0%)는 실제 툴 결과(4.39%)와 다르므로
    # verify.py가 경고로 잡아야 한다.
    assert len(result["verification_mismatches"]) == 1
    assert result["verification_mismatches"][0]["value"] == 100.0
    assert "검증 경고" in result["report_markdown"]


def test_run_agent_loop_writes_jsonl_log_with_all_events(monkeypatch, tmp_path):
    monkeypatch.setattr(loop, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(loop, "dispatch", lambda name, tool_input, backend=None: {"ok": True})

    date = "2026-09-23"
    backend = MockLLMBackend(build_demo_mock_script(date))
    loop.run_agent_loop(date, backend=backend)

    log_file = tmp_path / f"{date}.jsonl"
    assert log_file.exists()

    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    event_types = [e["type"] for e in events]

    assert event_types[0] == "run_start"
    assert event_types[-1] == "run_end"
    assert event_types.count("tool_call") == 3
    assert event_types.count("tool_result") == 3
    assert event_types.count("model_response") == 4

    tool_call_names = [e["name"] for e in events if e["type"] == "tool_call"]
    assert tool_call_names == ["get_market_snapshot", "get_anomalies", "search_news"]


def test_mock_backend_raises_when_script_exhausted():
    backend = MockLLMBackend([])
    try:
        backend.create_message(system="", messages=[], tools=[])
        assert False, "예외가 발생해야 한다"
    except RuntimeError as exc:
        assert "스크립트" in str(exc)


def test_default_backend_is_mock_without_llm_backend_env(monkeypatch):
    monkeypatch.setattr(loop, "LLM_BACKEND", "mock")
    backend = loop._default_backend("2026-09-23")
    assert isinstance(backend, MockLLMBackend)
