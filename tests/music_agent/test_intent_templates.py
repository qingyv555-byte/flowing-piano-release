from ai_piano_studio.music_agent.intent_templates import (
    QUICK_ACTION_TEMPLATES,
    get_quick_action_template,
)


def test_quick_action_catalog_is_curated_and_provider_agnostic() -> None:
    ids = [template.action_id for template in QUICK_ACTION_TEMPLATES]

    assert ids == [
        "continue",
        "improve_melody",
        "add_chords",
        "add_bass",
        "add_variation",
        "make_climax",
        "simplify",
        "sadder",
        "warmer",
        "dreamy",
    ]
    assert all(template.guidance for template in QUICK_ACTION_TEMPLATES)
    assert all("tool" not in " ".join(template.guidance).lower() for template in QUICK_ACTION_TEMPLATES)


def test_make_climax_template_preserves_motif_and_target_scope() -> None:
    template = get_quick_action_template("make_climax")

    assert template is not None
    assert template.label == "制造高潮"
    assert "保留可辨认的主题动机" in template.guidance
    assert "只修改用户指定或授权的范围" in template.guidance


def test_unknown_quick_action_is_not_silently_converted_to_a_write() -> None:
    assert get_quick_action_template("delete_everything") is None
