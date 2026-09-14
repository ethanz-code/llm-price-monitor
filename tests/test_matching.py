"""模型名匹配：版本号省略关系的边界与歧义取舍。"""
from llm_price_monitor.matching import resolve_site_names, version_omitted_match


def test_version_omitted_match_accepts_name_without_version():
    """models.dev 的目录 id 是 deepseek-flash，站点接口里叫 deepseek-v4.1-flash。"""
    assert version_omitted_match("deepseek-v4.1-flash", "deepseek-flash")
    assert version_omitted_match("deepseek-flash", "deepseek-v4.1-flash")
    assert version_omitted_match("deepseek-v4-flash-0731", "deepseek-flash")


def test_version_omitted_match_keeps_two_versions_apart():
    """两侧都带版本号就是两个模型，不能互相匹配。"""
    assert not version_omitted_match("deepseek-v4-flash", "deepseek-v4.1-flash")
    assert not version_omitted_match("gpt-5.6-sol", "gpt-5.6-terra")
    assert not version_omitted_match("glm-5.3-flash", "glm-4.7-flash")


def test_version_omitted_match_requires_same_family():
    assert not version_omitted_match("glm-5.3-flash", "deepseek-v4.1-flash")
    assert not version_omitted_match("deepseek-v4-flash-vision-exp", "deepseek-flash")
    assert not version_omitted_match("", "deepseek-flash")


def test_resolve_site_names_prefers_exact_then_claims_once():
    resolved = resolve_site_names(
        ["deepseek-v4-flash"],
        {"deepseek-flash": ("deepseek-flash",), "deepseek-v4-flash": ("deepseek-v4-flash",)},
    )

    assert resolved == {"deepseek-v4-flash": "deepseek-v4-flash"}


def test_resolve_site_names_gives_up_when_two_versions_are_present():
    resolved = resolve_site_names(
        ["deepseek-v4-flash", "deepseek-v4.1-flash"],
        {"deepseek-flash": ("deepseek-flash",)},
    )

    assert resolved == {}


def test_resolve_site_names_uses_ai_alias_before_version_rule():
    """AI 解析出的别名（此处是展示名）优先于版本号省略关系。"""
    resolved = resolve_site_names(
        ["DeepSeek V4.1 Flash"],
        {"deepseek-flash": ("deepseek-flash", "DeepSeek V4.1 Flash")},
    )

    assert resolved == {"deepseek-flash": "DeepSeek V4.1 Flash"}


def test_resolve_site_names_ignores_duplicate_records():
    """同一模型名在响应里重复出现不算多个候选。"""
    resolved = resolve_site_names(
        ["deepseek-v4.1-flash", "DeepSeek-V4.1-Flash"],
        {"deepseek-flash": ("deepseek-flash",)},
    )

    assert resolved == {"deepseek-flash": "deepseek-v4.1-flash"}
