from pathlib import Path


QML_DIR = Path(__file__).parents[2] / "src" / "ai_piano_studio" / "qml"


def test_first_run_has_direct_ai_and_audio_entry_actions() -> None:
    launch_source = (QML_DIR / "WelcomeLaunchView.qml").read_text(encoding="utf-8")
    welcome_source = (QML_DIR / "Welcome.qml").read_text(encoding="utf-8")
    main_source = (QML_DIR / "Main.qml").read_text(encoding="utf-8")

    assert "AI 帮我创作" in launch_source
    assert "导入 MIDI / 音频" in launch_source
    assert "signal aiRequested()" in welcome_source
    assert "signal importRequested()" in welcome_source
    assert "onAiRequested" in main_source
    assert "onImportRequested" in main_source


def test_wave1_editor_surface_exposes_beginner_actions_tips_and_selection_actions() -> None:
    main_source = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
    timeline_source = (QML_DIR / "Timeline.qml").read_text(encoding="utf-8")

    assert "BeginnerActionBar" in main_source
    assert "EditorTips" in main_source
    assert "SelectionActionBar" in main_source
    assert 'objectName: "editorActionRail"' in main_source
    assert "selectionActions" not in timeline_source
    assert 'sequence: "Ctrl+D"' in main_source
    assert 'sequence: "Ctrl+Shift+Up"' in main_source
    assert 'sequence: "Ctrl+Shift+Down"' in main_source
    assert 'sequence: "Left"' in main_source
    assert 'sequence: "Right"' in main_source
    assert 'sequence: "Up"' in main_source
    assert 'sequence: "Down"' in main_source
