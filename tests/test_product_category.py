from app.classifier.product_category import is_app_product_role


def test_explicit_app_product_manager_is_classified():
    assert is_app_product_role(
        job_title="APP 产品经理",
        job_summary="负责移动端产品规划",
        job_description=None,
        message_text="招聘 APP 产品经理",
    )


def test_mobile_product_manager_in_english_is_classified():
    assert is_app_product_role(
        job_title="Mobile Product Manager",
        job_summary=None,
        job_description="Own our iOS and Android app roadmap",
        message_text="Hiring a Mobile Product Manager",
    )


def test_other_role_with_android_on_another_line_is_not_classified():
    assert not is_app_product_role(
        job_title="产品经理",
        job_summary="负责后台管理产品",
        job_description=None,
        message_text="Android 开发工程师\n产品经理",
    )


def test_generic_product_manager_without_app_signal_is_not_classified():
    assert not is_app_product_role(
        job_title="产品经理",
        job_summary="负责支付产品规划",
        job_description=None,
        message_text="招聘远程产品经理",
    )
