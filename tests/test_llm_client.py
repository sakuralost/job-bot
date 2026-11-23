from app.classifier.llm_client import _escape_control_characters_in_strings


def test_unescaped_newline_inside_json_string_is_repaired():
    broken = '{"is_job_post": true, "reasoning_summary": "第一行\n第二行"}'
    repaired = _escape_control_characters_in_strings(broken)
    assert repaired == '{"is_job_post": true, "reasoning_summary": "第一行\\n第二行"}'


def test_json_whitespace_outside_strings_is_preserved():
    value = '{\n  "is_job_post": true,\n  "reasoning_summary": "正常"\n}'
    assert _escape_control_characters_in_strings(value) == value
