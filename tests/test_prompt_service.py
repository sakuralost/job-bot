import pytest

from app.services.prompt_service import DEFAULT_SYSTEM_PROMPT, validate_system_prompt


def test_default_prompt_has_required_output_contract():
    assert validate_system_prompt(DEFAULT_SYSTEM_PROMPT) == DEFAULT_SYSTEM_PROMPT
    assert sum("\u4e00" <= character <= "\u9fff" for character in DEFAULT_SYSTEM_PROMPT) >= 50


def test_prompt_without_required_fields_is_rejected():
    with pytest.raises(ValueError, match="缺少模型输出字段"):
        validate_system_prompt("只筛选远程产品岗位。" * 30)


def test_english_prompt_is_rejected():
    fields = " ".join(
        [
            "is_job_post",
            "is_product_role",
            "is_excluded",
            "work_mode",
            "is_remote",
            "contacts",
            "confidence",
            "reasoning_summary",
        ]
    )
    with pytest.raises(ValueError, match="必须使用中文"):
        validate_system_prompt(("Classify remote product manager jobs. " * 20) + fields)
