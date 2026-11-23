from app.services import source_policy


def test_excluded_company_aliases_are_normalized(monkeypatch, tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"excluded_company_aliases": ["Gate.io", "币安"]}', encoding="utf-8")
    monkeypatch.setattr(source_policy, "CONFIG_PATH", policy)
    source_policy.excluded_company_aliases.cache_clear()

    assert source_policy.is_excluded_company("Gate.io") is True
    assert source_policy.is_excluded_company("币安") is True
    assert source_policy.is_excluded_company("Small Builder") is False

    source_policy.excluded_company_aliases.cache_clear()
