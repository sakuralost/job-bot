from types import SimpleNamespace

from worker import classify_pending
from worker.classify_pending import (
    PRODUCT_CONSISTENCY_PROMPT,
    _chinese_role_match_reason,
    _has_explicit_product_role,
    _keep_for_manual_review,
    _resolve_product_consistency,
)


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


def test_consistency_prompt_keeps_product_identity_separate_from_exclusion():
    assert "is_product_role 必须为 true" in PRODUCT_CONSISTENCY_PROMPT
    assert "is_excluded=true" in PRODUCT_CONSISTENCY_PROMPT
    assert "不能把产品岗位改判为 is_product_role=false" in PRODUCT_CONSISTENCY_PROMPT


def test_contradictory_model_result_is_kept_for_manual_review():
    result = SimpleNamespace(
        is_job_post=False,
        is_product_role=False,
        is_excluded=True,
        excluded_category="其他",
        excluded_reason="模型误判",
        job_title=None,
        is_remote=None,
        work_mode="unknown",
        confidence=0.9,
        role_match_reason=None,
        reasoning_summary="不是产品岗位",
    )
    reviewed = _keep_for_manual_review(result, "招聘远程 AI 产品经理")
    assert reviewed.is_job_post is True
    assert reviewed.is_product_role is True
    assert reviewed.is_excluded is False
    assert reviewed.is_remote is True
    assert reviewed.confidence == 0.5
    assert "人工复核" in reviewed.role_match_reason


def test_excluded_product_result_stays_excluded():
    result = SimpleNamespace(
        is_job_post=True,
        is_product_role=False,
        is_excluded=True,
        excluded_category="理财产品经理",
        excluded_reason="命中产品经理细分排除项",
    )

    reviewed = _resolve_product_consistency(result, "招聘远程理财产品经理")

    assert reviewed.is_product_role is True
    assert reviewed.is_excluded is True
    assert reviewed.excluded_reason == "命中产品经理细分排除项"


def test_generic_product_mention_trusts_llm_result():
    result = SimpleNamespace(
        is_job_post=True,
        is_product_role=False,
        is_excluded=False,
    )

    reviewed = _resolve_product_consistency(result, "招聘电商运营，负责产品优化")

    assert reviewed is result
    assert reviewed.is_product_role is False
    assert reviewed.is_excluded is False
    assert not _has_explicit_product_role("招聘【产品】和市场运营")
    assert _has_explicit_product_role("招聘远程产品经理")
