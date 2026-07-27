from types import SimpleNamespace

from worker import classify_pending
from worker.classify_pending import _chinese_role_match_reason


def test_english_model_reason_is_converted_before_save():
    result = SimpleNamespace(
        role_match_reason="Strong product manager match",
        work_mode="remote",
        location="Singapore",
        confidence=0.93,
    )
    reason = _chinese_role_match_reason(result)
    assert "产品经理" in reason
    assert "远程" in reason
    assert "93%" in reason


def test_chinese_model_reason_is_preserved_before_save():
    result = SimpleNamespace(
        role_match_reason="岗位职责明确属于产品经理，并支持远程办公",
        work_mode="remote",
        location=None,
        confidence=0.88,
    )
    assert _chinese_role_match_reason(result) == result.role_match_reason


def test_new_job_prompt_includes_manual_ignore_feedback(monkeypatch):
    monkeypatch.setattr(
        classify_pending,
        "ignored_reason_context",
        lambda db: "\n人工忽略反馈：不考虑增长产品经理",
    )
    prompt = classify_pending._personalized_prompt(object(), "基础筛选提示词")
    assert prompt == "基础筛选提示词\n人工忽略反馈：不考虑增长产品经理"
