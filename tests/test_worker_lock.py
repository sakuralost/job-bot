from app.services.worker_lock import llm_classification_lock, telegram_scan_lock


def test_second_classifier_skips_while_lock_is_held(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_BOT_LLM_LOCK_PATH", str(tmp_path / "llm.lock"))
    with llm_classification_lock(blocking=True) as first:
        assert first
        with llm_classification_lock(blocking=False) as second:
            assert not second


def test_second_scan_skips_while_scan_lock_is_held(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_BOT_SCAN_LOCK_PATH", str(tmp_path / "scan.lock"))
    with telegram_scan_lock(blocking=True) as first:
        assert first
        with telegram_scan_lock(blocking=False) as second:
            assert not second
