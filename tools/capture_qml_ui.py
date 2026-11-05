"""Capture named AI Piano Studio Qt Quick states using native Qt screenshots."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile


SUPPORTED_STATES = {
    "launch", "launch-recovery", "library", "library-empty",
    "library-management", "editor-beginner", "editor-advanced", "editor-focus",
    "editor-sidebar-icon", "minimap-weak",
    "selection-collapsed", "selection-empty", "selection-single",
    "selection-multi", "music-layers", "arrangement-expanded", "empty-project",
    "long-project-name",
    "editor-playful", "editor-music-settings", "background-settings",
    "inspector-collapsed", "playing", "recording",
    "unsynced", "error-toast", "low-motion", "dynamic-disabled",
    "harmony-off", "harmony-preview-collapsed", "harmony-preview-expanded",
    "harmony-preview-temporary", "harmony-auto", "harmony-help",
    "harmony-help-5", "harmony-help-11", "harmony-help-12",
    "harmony-help-selected", "settings-save", "unsaved-exit",
    "harmony-melody-c-major", "harmony-melody-a-minor",
    "harmony-melody-f-major", "harmony-melody-cadence", "audio-import", "support",
    "proposal-inspector", "proposal-overlay", "proposal-velocity-66",
}
SUPPORTED_SIZES = ((1280, 720), (1366, 768), (1920, 1080), (2560, 1440))

HARMONY_MELODY_STATES = {
    "harmony-melody-c-major": ((72, 0.0), (76, 0.5), (79, 1.0), (72, 1.5)),
    "harmony-melody-a-minor": ((69, 0.0), (72, 0.5), (76, 1.0), (69, 1.5)),
    "harmony-melody-f-major": ((65, 0.0), (69, 0.5), (72, 1.0), (65, 1.5)),
    "harmony-melody-cadence": ((67, 0.0), (71, 0.5), (74, 1.0), (72, 1.5)),
}


def _build_shell(
    project_root: Path,
    temporary: Path,
    recovery: bool,
    populate_library: bool,
    state: str | None = None,
):
    source = str((project_root / "src").resolve())
    if source in sys.path:
        sys.path.remove(source)
    sys.path.insert(0, source)

    from PySide6.QtCore import QSettings

    from ai_piano_studio.__main__ import build_engine
    from ai_piano_studio.domain.note import Note
    from ai_piano_studio.domain.project import ProjectState
    from ai_piano_studio.editor.controller import EditorController
    from ai_piano_studio.experience.settings import ExperienceSettings
    from ai_piano_studio.harmony.settings import HarmonySettings
    from ai_piano_studio.persistence.autosave import NullRecoveryStore, RecoveryStore
    from ai_piano_studio.persistence.project_json import save_project
    from ai_piano_studio.persistence.project_library import ProjectLibrary
    from ai_piano_studio.persistence.recent_files import RecentFiles
    from ai_piano_studio.persistence.save_preferences import SavePreferences

    melody = HARMONY_MELODY_STATES.get(state)
    if melody is None and state is not None and state.startswith("harmony-"):
        melody = HARMONY_MELODY_STATES["harmony-melody-c-major"]
    if state == "empty-project":
        notes = ()
    elif state in {
        "editor-playful", "editor-music-settings", "background-settings",
        "selection-multi", "music-layers", "arrangement-expanded",
    }:
        # Representative synthetic melody for layout inspection, not user data.
        notes = tuple(
            Note.create(id=f"stage-{index}", pitch=(60, 64, 67, 72, 69, 67)[index % 6],
                        start_time=index * 0.5, duration=0.4)
            for index in range(24)
        )
    elif state == "proposal-velocity-66":
        notes = tuple(
            Note.create(
                id=f"ui-capture-velocity-{index}",
                pitch=48 + (index * 5) % 29,
                start_time=index * (8.0 / 66.0),
                duration=0.18,
                velocity=78 + index % 3,
            )
            for index in range(66)
        )
    elif melody is None:
        notes = (
            Note.create(id="ui-capture-demo", pitch=60, start_time=0.0, duration=8.0),
        )
    else:
        notes = tuple(
            Note.create(
                id=f"ui-capture-melody-{index}",
                pitch=pitch,
                start_time=start,
                duration=0.45,
                velocity=78,
            )
            for index, (pitch, start) in enumerate(melody)
        )
    layers = None
    if state in {"music-layers", "arrangement-expanded"}:
        from ai_piano_studio.domain.music_layer import MusicLayer

        layers = tuple(
            MusicLayer.create(id=layer_id, name=name, role=role, order=index)
            for index, (layer_id, name, role) in enumerate((
                ("melody", "主旋律", "melody"),
                ("accompaniment", "伴奏", "accompaniment"),
                ("bass", "低音", "bass"),
                ("drums", "节奏", "drums"),
            ))
        )
        notes = tuple(
            note.with_changes(layer_id=layers[index % len(layers)].id)
            for index, note in enumerate(notes)
        )
    selected_ids = (
        frozenset()
        if state in {"selection-empty", "empty-project"}
        else (frozenset(note.id for note in notes)
              if state == "selection-multi"
              else frozenset({notes[0].id}))
    )
    project_state = ProjectState(
        notes, selected_ids, layers=layers or (MusicLayer.default(),)
    ) if layers is not None else ProjectState(notes, selected_ids)
    recovery_store = RecoveryStore(temporary / "recovery") if recovery else NullRecoveryStore()
    if recovery:
        recovery_store.write(project_state, None)
    library = ProjectLibrary(temporary / "projects")
    if populate_library:
        project_path = library.root / "project-001" / "project.pianoai.json"
        project_path.parent.mkdir(parents=True)
        save_project(project_path, project_state)
        display_name = (
            "月光穿过很长很长的海岸线与群山之间的练习作品"
            if state == "long-project-name"
            else "界面验证示例"
        )
        library.set_display_name(project_path, display_name)
    controller = EditorController(
        project_state,
        recent_files=RecentFiles(QSettings(str(temporary / "recent.ini"), QSettings.IniFormat)),
        project_library=library,
        recovery_store=recovery_store,
    )
    experience_settings = QSettings(str(temporary / "experience.ini"), QSettings.IniFormat)
    appearance_settings = QSettings(str(temporary / "appearance.ini"), QSettings.IniFormat)
    harmony_settings = HarmonySettings(
        settings=QSettings(str(temporary / "harmony.ini"), QSettings.IniFormat)
    )
    save_preferences = SavePreferences(
        settings=QSettings(str(temporary / "save.ini"), QSettings.IniFormat)
    )
    if state == "settings-save":
        save_preferences.setAutosaveEnabled(True)
        save_preferences.setAutosaveDelaySeconds(120)
    app, engine, controller, playback, keyboard = build_engine(
        controller=controller,
        experience_settings=ExperienceSettings(settings=experience_settings),
        harmony_settings=harmony_settings,
        save_preferences=save_preferences,
        appearance_settings=appearance_settings,
    )
    return app, engine, engine.rootObjects()[0], controller, playback, keyboard


def _enter_editor(root, experience, mode: str = "beginner") -> None:
    from PySide6.QtCore import Q_ARG, QMetaObject, Qt

    assert QMetaObject.invokeMethod(
        root, "setPage", Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", "editor"), Q_ARG("QVariant", False),
    )
    experience.setMode(mode)


def _harmony_result(project_identity: str, state: str):
    from ai_piano_studio.harmony.types import (
        ChordCandidate,
        HarmonyAnalysisResult,
        KeyEstimate,
        MeasureSuggestion,
        ScoreBreakdown,
    )

    result_specs = {
        "harmony-melody-a-minor": (
            (9, "A", "minor"),
            (
                ("Am", "minor", (45, 48, 52)),
                ("Dm", "minor", (50, 53, 57)),
                ("E", "major", (40, 44, 47)),
            ),
        ),
        "harmony-melody-f-major": (
            (5, "F", "major"),
            (
                ("F", "major", (41, 45, 48)),
                ("Bb", "major", (46, 50, 53)),
                ("Dm", "minor", (50, 53, 57)),
            ),
        ),
        "harmony-melody-cadence": (
            (0, "C", "major"),
            (
                ("G", "major", (43, 47, 50)),
                ("C", "major", (48, 52, 55)),
                ("Am", "minor", (45, 48, 52)),
            ),
        ),
    }
    key, candidates = result_specs.get(
        state,
        (
            (0, "C", "major"),
            (
                ("C", "major", (48, 52, 55)),
                ("F", "major", (41, 45, 48)),
                ("Am", "minor", (45, 48, 52)),
            ),
        ),
    )
    scores = (0.86, 0.74, 0.68)
    built_candidates = tuple(
        ChordCandidate(
            symbol,
            quality,
            index,
            pitches,
            52,
            ScoreBreakdown(0.8, 0.8, 0.0, 0.0, 0.2, 0.7, 0.9, 0.0, score, score),
        )
        for index, ((symbol, quality, pitches), score) in enumerate(
            zip(candidates, scores, strict=True)
        )
    )
    temporary = state == "harmony-preview-temporary"
    return HarmonyAnalysisResult(
        project_identity,
        0,
        0,
        "preview",
        KeyEstimate(key[0], key[1], key[2], 0.82, False, temporary),
        (MeasureSuggestion(0, 0, 1_920, temporary, built_candidates),),
    )


def _apply_harmony_state(state: str, root, engine) -> None:
    from PySide6.QtCore import QObject

    experience = engine.rootContext().contextProperty("experience")
    harmony = engine.rootContext().contextProperty("harmony")
    help_content = engine.rootContext().contextProperty("harmonyHelp")
    _enter_editor(root, experience)
    if state == "harmony-off":
        harmony.setMode("off")
        return
    if state == "harmony-auto":
        harmony.setMode("auto")
        return
    help_sections = {
        "harmony-help": "harmony-basics",
        "harmony-help-5": "chord-selection",
        "harmony-help-11": "climax-design",
        "harmony-help-12": "cadence-ending",
        "harmony-help-selected": "chord-selection",
    }
    if state in help_sections:
        harmony.setMode("off")
        help_content.openSection(help_sections[state])
        root.openHarmonyHelp(help_sections[state], None)
        return
    harmony.setMode("preview")
    harmony.previewModel.apply_result(_harmony_result(harmony._project_identity, state))
    lane = root.findChild(QObject, "harmonySuggestionLane")
    lane.setProperty(
        "playheadSeconds", 3.0 if state == "harmony-preview-collapsed" else 0.5
    )


def _apply_state(state: str, root, engine, controller, playback, keyboard) -> None:
    """Apply only public UI/controller state to an isolated temporary shell."""
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt
    from PySide6.QtTest import QTest

    scene = engine.rootContext().contextProperty("scene")
    experience = engine.rootContext().contextProperty("experience")
    welcome = root.findChild(QObject, "welcomeOverlay")
    if state in {
        "editor-playful", "editor-music-settings", "background-settings", "music-layers"
    }:
        _enter_editor(root, experience)
        if state in {"editor-music-settings", "background-settings"}:
            assert QMetaObject.invokeMethod(
                root, "openAppearancePopup", Qt.ConnectionType.DirectConnection,
                Q_ARG("QVariant", None),
            )
            QTest.qWait(50)
        elif state == "music-layers":
            experience.setArrangementVisible(True)
            inspector = root.findChild(QObject, "inspector")
            if inspector is not None:
                QMetaObject.invokeMethod(
                    inspector, "showLayerContext", Qt.ConnectionType.DirectConnection
                )
        return
    if state == "arrangement-expanded":
        _enter_editor(root, experience)
        experience.setArrangementVisible(True)
        return
    if state in {"proposal-inspector", "proposal-overlay", "proposal-velocity-66"}:
        from ai_piano_studio.music_agent.tool_contracts import BarRange, MusicPermission

        _enter_editor(root, experience)
        assistant = engine.rootContext().contextProperty("assistant")
        context = assistant.gateway.create_execution_context(
            authorized_bars=(BarRange(1, 5),),
            permissions=frozenset({MusicPermission.WRITE_NOTES}),
        )
        if state == "proposal-velocity-66":
            result = assistant.gateway.execute(
                "set_note_velocity",
                {
                    "note_ids": [note.id for note in controller.state.active_notes],
                    "velocity": 66,
                },
                context,
            )
        else:
            result = assistant.gateway.execute(
                "add_notes",
                {
                    "notes": [
                        {"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96},
                        {"pitch": 76, "start_time": 3.25, "duration": 0.75, "velocity": 108},
                    ]
                },
                context,
            )
        if not result.success:
            raise RuntimeError(f"unable to stage proposal capture: {result.summary}")
        assistant.presentProposal(context)
        if state in {"proposal-inspector", "proposal-velocity-66"}:
            assistant.openWindow()
            QTest.qWait(100)
            dialog = root.findChild(QObject, "assistantProposalChangesDialog")
            if dialog is None:
                raise RuntimeError("proposal inspector dialog was not created")
            dialog.setProperty("visible", True)
        return
    if state.startswith("harmony-"):
        _apply_harmony_state(state, root, engine)
        return
    if state == "settings-save":
        _enter_editor(root, experience)
        settings = root.findChild(QObject, "backgroundSettingsDialog")
        assert settings is not None
        assert QMetaObject.invokeMethod(
            root, "openAppearancePopup", Qt.ConnectionType.DirectConnection,
            Q_ARG("QVariant", None),
        )
        QTest.qWait(50)
        return
    if state == "unsaved-exit":
        _enter_editor(root, experience)
        controller.setBpm(90)
        opener = root.findChild(QObject, "backToLibraryButton")
        assert opener is not None
        assert QMetaObject.invokeMethod(
            root, "requestLibraryExit", Qt.ConnectionType.DirectConnection,
            Q_ARG("QVariant", "library"), Q_ARG("QVariant", opener),
        )
        return
    if state == "launch" or state == "launch-recovery":
        return
    if state == "library":
        assert QMetaObject.invokeMethod(
            root, "setPage", Qt.ConnectionType.DirectConnection,
            Q_ARG("QVariant", "library"), Q_ARG("QVariant", False),
        )
        return
    if state == "library-empty":
        assert QMetaObject.invokeMethod(
            root, "setPage", Qt.ConnectionType.DirectConnection,
            Q_ARG("QVariant", "library"), Q_ARG("QVariant", False),
        )
        return
    if state == "library-management":
        assert QMetaObject.invokeMethod(
            root, "setPage", Qt.ConnectionType.DirectConnection,
            Q_ARG("QVariant", "library"), Q_ARG("QVariant", False),
        )
        welcome.setProperty("libraryManaging", True)
        return
    if state == "editor-beginner":
        _enter_editor(root, experience)
        return
    if state == "editor-advanced":
        _enter_editor(root, experience, "advanced")
        return
    if state == "editor-focus":
        _enter_editor(root, experience, "advanced")
        root.setProperty("focusMode", True)
        return
    if state == "editor-sidebar-icon":
        _enter_editor(root, experience, "advanced")
        experience.setSidebarMode("icon")
        return
    if state == "minimap-weak":
        _enter_editor(root, experience, "advanced")
        experience.setMiniMapDisplayMode("weak")
        return
    if state in {"selection-empty", "selection-single", "selection-multi"}:
        _enter_editor(root, experience)
        return
    if state == "empty-project":
        _enter_editor(root, experience)
        return
    if state == "long-project-name":
        entries = controller.libraryEntries
        if not entries:
            raise RuntimeError("long-project-name capture requires a library project")
        controller.loadProject(str(entries[0]["path"]))
        _enter_editor(root, experience)
        # Let the normal transient "opened" feedback clear so the capture
        # isolates title elision instead of reviewing an unrelated toast.
        QTest.qWait(2200)
        return
    if state == "selection-collapsed":
        _enter_editor(root, experience)
        toggle = root.findChild(QObject, "selectionActionsToggle")
        assert toggle is not None
        toggle.clicked.emit()
        return
    if state == "audio-import":
        _enter_editor(root, experience)
        root.setProperty("audioImportDialogRequested", True)
        QTest.qWait(100)
        dialog = root.findChild(QObject, "audioImportDialog")
        assert dialog is not None
        assert QMetaObject.invokeMethod(
            dialog, "open", Qt.ConnectionType.DirectConnection
        )
        return
    if state == "support":
        root.setProperty("supportDialogRequested", True)
        QTest.qWait(100)
        dialog = root.findChild(QObject, "supportDialog")
        assert dialog is not None
        assert QMetaObject.invokeMethod(
            dialog, "open", Qt.ConnectionType.DirectConnection
        )
        return
    if state == "inspector-collapsed":
        _enter_editor(root, experience)
        root.findChild(QObject, "inspector").setProperty("collapsed", True)
        return
    if state == "playing":
        _enter_editor(root, experience)
        playback.play()
        return
    if state == "recording":
        _enter_editor(root, experience)
        keyboard.startRecording()
        return
    if state == "unsynced":
        _enter_editor(root, experience)
        controller.createNoteAt(160.0, 180.0)
        QMetaObject.invokeMethod(root, "togglePlayback", Qt.ConnectionType.DirectConnection)
        return
    if state == "error-toast":
        _enter_editor(root, experience)
        root.showFeedback("danger", "无法打开工程文件", {})
        return
    if state == "low-motion":
        scene.setMotionLevel("low")
        return
    if state == "dynamic-disabled":
        scene.setDynamicEnabled(False)
        return
    raise ValueError(f"unsupported state: {state}")


def _sample_count(
    image,
    logical_width: int,
    logical_height: int,
    dpr: float,
    logical_step: int = 64,
) -> int:
    """Count colours on a logical grid, not directly in physical pixels."""
    colors: set[int] = set()
    for y in range(0, logical_height, logical_step):
        pixel_y = min(image.height() - 1, round(y * dpr))
        for x in range(0, logical_width, logical_step):
            pixel_x = min(image.width() - 1, round(x * dpr))
            colors.add(image.pixelColor(pixel_x, pixel_y).rgba())
    return len(colors)


def capture(project_root: Path, output: Path, width: int, height: int, state: str, scale: float) -> None:
    """Create one validation-checked native grabWindow PNG."""
    if (width, height) not in SUPPORTED_SIZES:
        raise ValueError(f"unsupported logical size: {width}x{height}")
    if state not in SUPPORTED_STATES:
        raise ValueError(f"unsupported state: {state}")
    if scale <= 0:
        raise ValueError("--scale must be greater than zero")
    if not (project_root / "src").is_dir():
        raise ValueError("--project-root must contain src")
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    with tempfile.TemporaryDirectory(prefix="ai-piano-ui-capture-") as temporary_name:
        app, engine, root, controller, playback, keyboard = _build_shell(
            project_root,
            Path(temporary_name),
            state == "launch-recovery",
            state in {"library", "library-management", "long-project-name"},
            state,
        )
        root.setProperty("width", width)
        root.setProperty("height", height)
        root.setProperty("visible", True)
        from PySide6.QtTest import QTest

        QTest.qWaitForWindowExposed(root, 1000)
        app.processEvents()
        _apply_state(state, root, engine, controller, playback, keyboard)
        app.processEvents()
        QTest.qWait(1200)
        capture_window = root
        if state in {"proposal-inspector", "proposal-velocity-66"}:
            from PySide6.QtCore import QObject

            assistant_window = root.findChild(QObject, "assistantWindow")
            if assistant_window is None:
                raise RuntimeError("assistant window was not created")
            assistant_window.setProperty("width", width)
            assistant_window.setProperty("height", height)
            assistant_window.setProperty("visible", True)
            QTest.qWaitForWindowExposed(assistant_window, 1000)
            app.processEvents()
            capture_window = assistant_window
        image = capture_window.grabWindow()
        if image.isNull():
            raise RuntimeError("QQuickWindow.grabWindow() returned a null image")
        # grabWindow() returns physical pixels but the returned QImage may not
        # retain the window DPR, especially with the offscreen platform plugin.
        dpr = root.devicePixelRatio() or image.devicePixelRatio() or 1.0
        logical_width = round(image.width() / dpr)
        logical_height = round(image.height() / dpr)
        if (logical_width, logical_height) != (width, height):
            raise RuntimeError(
                f"capture has logical size {logical_width}x{logical_height}, expected {width}x{height}"
            )
        logical_step = 32 if state.startswith("harmony-") else 64
        colors = _sample_count(image, width, height, dpr, logical_step)
        if colors < 16:
            raise RuntimeError(
                f"capture has only {colors} distinct {logical_step}px-grid samples"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(output)):
            raise RuntimeError(f"unable to save capture: {output}")
        if capture_window is not root:
            capture_window.close()
        root.close()
        engine.deleteLater()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--state", choices=sorted(SUPPORTED_STATES), required=True)
    parser.add_argument("--scale", type=float, default=1.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        capture(
            args.project_root.resolve(), args.output, args.width, args.height,
            args.state, args.scale,
        )
        print(args.output)
        return 0
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
