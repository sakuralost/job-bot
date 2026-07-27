import json
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.dashboard import _display_reason, _list_scope, _workflow_snapshot, templates, update_status
from app.database.models import JobDedupGroup, JobPost


def test_english_legacy_reason_gets_chinese_display_fallback():
    job = SimpleNamespace(
        role_match_reason="Strong product manager match",
        work_mode="remote",
        location="Singapore",
        confidence=0.91,
    )
    reason = _display_reason(job)
    assert "产品经理" in reason
    assert "远程" in reason
    assert "91%" in reason


def test_existing_chinese_reason_is_preserved():
    job = SimpleNamespace(
        role_match_reason="岗位职责明确属于产品经理，并支持远程办公",
        work_mode="remote",
        location=None,
        confidence=0.88,
    )
    assert _display_reason(job) == job.role_match_reason


def test_dashboard_template_contains_live_progress_and_incremental_scan_button():
    template = templates.env.get_template("dashboard.html")
    source = Path(template.filename).read_text(encoding="utf-8")
    assert 'id="reclass-progress"' in source
    assert 'action="/scan-now"' in source
    assert 'fetch("/api/workflow-status"' in source
    assert "上次成功记录的位置继续" in source
    assert 'id="manual-progress"' in source
    assert "LLM 已筛" in source
    assert "剩余 ${manual.remaining} 条" in source
    assert "setInterval(refreshWorkflow, 2000)" in source
    assert 'action="/settings/scan-schedule"' in source
    assert 'id="scan-count"' in source
    assert 'type="time"' in source


def test_app_category_only_uses_pending_jobs():
    assert _list_scope("", "app") == ("new", "", "app")


def test_manual_state_takes_priority_over_app_category():
    assert _list_scope("ignored", "app") == ("ignored", "ignored", "")
    assert _list_scope("contacted", "app") == ("contacted", "contacted", "")


def test_dashboard_explains_manual_decision_priority():
    template = templates.env.get_template("dashboard.html")
    source = Path(template.filename).read_text(encoding="utf-8")
    assert "处理过的岗位会离开候选区" in source


def test_status_actions_update_without_page_reload():
    template = templates.env.get_template("dashboard.html")
    source = Path(template.filename).read_text(encoding="utf-8")
    assert 'event.preventDefault()' in source
    assert 'headers: {"Accept": "application/json"}' in source
    assert 'card.remove()' in source
    assert "操作失败，请重试。" in source


def test_model_summary_is_open_by_default():
    template = templates.env.get_template("dashboard.html")
    source = Path(template.filename).read_text(encoding="utf-8")
    assert '<details class="model-details" open>' in source
    assert "<summary>模型整理的摘要</summary>" in source


def test_llm_prompt_settings_can_be_collapsed_after_opening():
    template = templates.env.get_template("dashboard.html")
    source = Path(template.filename).read_text(encoding="utf-8")
    assert 'class="settings-toggle-hint"' in source
    assert 'content:"点击收起"' in source
    assert 'id="collapse-llm-settings"' in source
    assert "llmSettings.open = false" in source
    assert "history.replaceState" in source


def test_manual_workflow_snapshot_exposes_remaining_count_and_finish_time():
    manual = SimpleNamespace(
        id=9,
        status="completed",
        classification_total=12,
        classification_processed=12,
        llm_started_at=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc),
        messages_found=20,
        rule_candidates=12,
        scan_error_count=0,
        accepted_items=4,
        excluded_items=6,
        duplicate_items=2,
        error_items=0,
        finished_at=datetime(2026, 7, 27, 10, 30, tzinfo=timezone.utc),
    )

    class FakeDb:
        def __init__(self):
            self.values = iter([None, manual, None])

        def scalar(self, _query):
            return next(self.values)

    snapshot = _workflow_snapshot(FakeDb())["manual_scan"]
    assert snapshot["remaining"] == 0
    assert snapshot["percent"] == 100
    assert snapshot["finished_at"] == "07 月 27 日 18:30:00"


def test_status_endpoint_returns_json_for_inline_update():
    job = SimpleNamespace(id=42, status="new", dedup_group_id=9)
    group = SimpleNamespace(id=9, status="new", note=None)

    class FakeDb:
        def get(self, model, item_id):
            if model is JobPost:
                assert item_id == 42
                return job
            assert model is JobDedupGroup
            assert item_id == 9
            return group

        def commit(self):
            pass

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/opportunities/42/status",
        "headers": [(b"accept", b"application/json")],
    })
    response = update_status(
        request=request,
        job_id=42,
        value="ignored",
        ignore_reason="岗位方向不符合当前目标",
        return_state="",
        return_category="app",
        db=FakeDb(),
        _="admin",
    )
    payload = json.loads(response.body)
    assert payload == {
        "ok": True,
        "job_id": 42,
        "previous_status": "new",
        "status": "ignored",
        "remove": True,
    }
    assert job.status == "ignored"
    assert group.status == "ignored"
    assert group.note == "岗位方向不符合当前目标"


def test_ignore_reason_form_is_inline_and_required():
    template = templates.env.get_template("dashboard.html")
    source = Path(template.filename).read_text(encoding="utf-8")
    assert 'class="ignore-trigger" type="button"' in source
    assert 'name="ignore_reason"' in source
    assert "会记录并用于后续岗位的模型匹配" in source
    assert "请填写忽略原因。" in source


def test_status_endpoint_rejects_empty_ignore_reason():
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/opportunities/42/status",
        "headers": [(b"accept", b"application/json")],
    })
    with pytest.raises(HTTPException, match="请填写忽略原因"):
        update_status(
            request=request,
            job_id=42,
            value="ignored",
            ignore_reason="  ",
            return_state="",
            return_category="",
            db=object(),
            _="admin",
        )
