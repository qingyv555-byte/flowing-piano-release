from ai_piano_studio.persistence.project_naming import inspect_project_name


def test_project_name_is_trimmed_and_windows_invalid_chars_are_replaced() -> None:
    result = inspect_project_name('  Piano:Night/01*  ')

    assert result.valid is True
    assert result.normalized == "Piano_Night_01_"


def test_project_name_rejects_empty_and_reserved_windows_names() -> None:
    assert inspect_project_name("   ").valid is False
    assert inspect_project_name("CON").valid is False


def test_duplicate_name_gets_a_visible_non_overwriting_suggestion() -> None:
    result = inspect_project_name("练习", existing_names=["练习"])

    assert result.valid is False
    assert result.suggestion == "练习 2"


def test_current_name_can_be_kept_when_checking_rename_collisions() -> None:
    result = inspect_project_name(
        "原名", existing_names=["原名", "另一首"], current_name="原名"
    )

    assert result.valid is True
    assert result.normalized == "原名"
