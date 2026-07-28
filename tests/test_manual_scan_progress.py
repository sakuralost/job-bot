from types import SimpleNamespace

from worker import manual_scan


class FakeSession:
    def __init__(self, request):
        self.request = request
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, _model, request_id):
        assert request_id == self.request.id
        return self.request

    def commit(self):
        self.committed = True


def test_reading_progress_is_persisted_for_live_dashboard(monkeypatch):
    request = SimpleNamespace(id=7, messages_found=0, rule_candidates=0)
    session = FakeSession(request)
    monkeypatch.setattr(manual_scan, "SessionLocal", lambda: session)

    manual_scan._update_scan_progress(7, messages_found=23, rule_candidates=5)

    assert request.messages_found == 23
    assert request.rule_candidates == 5
    assert session.committed is True


def test_resumed_llm_progress_keeps_previous_counts():
    merged = manual_scan._merged_counts(
        {"accepted": 3, "excluded": 2, "duplicate": 1, "error": 0},
        {
            "accepted": 4,
            "excluded": 1,
            "excluded_not_remote": 2,
            "not_product_role": 3,
            "duplicate": 2,
            "error": 1,
        },
    )

    assert merged == {"accepted": 7, "excluded": 8, "duplicate": 3, "error": 1}
