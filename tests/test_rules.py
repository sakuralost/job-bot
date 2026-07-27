from app.classifier.rule_filter import decide


FILTERS = {
    "target_roles": {"include": ["产品经理", "product manager"]},
    "recruitment_intent": {"include": ["招聘", "hiring"]},
    "excluded_industries": {"hard_keywords": ["博彩", "gambling"]},
}


def test_role_and_recruitment_signal_are_both_required():
    assert decide("招聘远程产品经理", FILTERS).candidate
    assert not decide("高级产品经理，欢迎交流", FILTERS).candidate


def test_hard_exclusion_wins():
    result = decide("博彩平台招聘远程产品经理", FILTERS)
    assert result.hard_rejected
    assert not result.candidate


def test_negated_exclusion_is_sent_to_llm():
    result = decide("不涉及博彩，招聘远程产品经理", FILTERS)
    assert result.candidate
    assert not result.hard_rejected
