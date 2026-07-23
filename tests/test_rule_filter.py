from app.classifier.rule_filter import decide

FILTERS = {
    "target_roles": {"include": ["产品经理", "product manager"]},
    "recruitment_intent": {"include": ["招聘", "hiring"]},
    "excluded_industries": {"hard_keywords": ["赌博", "gambling"]},
}


def test_hard_excluded_keyword_wins():
    result = decide("招聘产品经理，赌博公司背景", FILTERS)
    assert result.hard_rejected
    assert not result.candidate


def test_role_and_recruitment_signal_both_required():
    result = decide("产品经理，欢迎投递", FILTERS)  # 没有招聘信号词
    assert not result.candidate
    assert not result.hard_rejected


def test_role_and_recruitment_match_is_candidate():
    result = decide("招聘产品经理1名，远程办公", FILTERS)
    assert result.candidate
    assert not result.hard_rejected


def test_negated_exclusion_passed_to_llm_instead_of_dropped():
    result = decide("招聘产品经理，非赌博行业，远程", FILTERS)
    assert not result.hard_rejected  # 明确否定的排除词交给 LLM 判断，不在规则层直接丢弃
