from tools.capture_qml_ui import _parser


def test_capture_cli_accepts_unsaved_exit_autosave_evidence_states() -> None:
    for state in (
        "settings-save",
        "unsaved-exit",
        "harmony-help-selected",
        "proposal-velocity-66",
    ):
        arguments = _parser().parse_args(
            [
                "--state",
                state,
                "--width",
                "1280",
                "--height",
                "720",
                "--output",
                "capture.png",
            ]
        )
        assert arguments.state == state


def test_capture_cli_uses_the_icon_sidebar_state_name() -> None:
    arguments = _parser().parse_args(
        [
            "--state",
            "editor-sidebar-icon",
            "--width",
            "1280",
            "--height",
            "720",
            "--output",
            "capture.png",
        ]
    )
    assert arguments.state == "editor-sidebar-icon"


def test_capture_cli_accepts_long_project_name_fidelity_state() -> None:
    arguments = _parser().parse_args(
        [
            "--state",
            "long-project-name",
            "--width",
            "1366",
            "--height",
            "768",
            "--output",
            "capture.png",
        ]
    )
    assert arguments.state == "long-project-name"


def test_capture_cli_accepts_empty_project_fidelity_state() -> None:
    arguments = _parser().parse_args(
        [
            "--state",
            "empty-project",
            "--width",
            "1366",
            "--height",
            "768",
            "--output",
            "capture.png",
        ]
    )
    assert arguments.state == "empty-project"
