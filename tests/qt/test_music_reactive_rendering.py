"""A/B draw actual PCM/session input and leave music and input surfaces intact."""
from PySide6.QtCore import QObject, QRect
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

from ai_piano_studio.audio.visualizer import AudioVisualizerController, PcmSnapshotTap
from tests.qt.test_studio_feedback import rig, advance, note, load_feedback
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_nocturne import preserved_qsettings_state


def test_study_entry_opens_editable_copy_and_preserves_previous_copy(studio_session, tmp_path, monkeypatch):
    from ai_piano_studio.music_studies import prepare_study
    from ai_piano_studio.persistence.project_document import load_project_document
    app, engine, root, editor, playback = studio_session
    monkeypatch.chdir(tmp_path.parent)
    first = prepare_study(engine, editor, type(tmp_path)(tmp_path.name), "string")
    before = first.read_bytes()
    assert editor.currentFilePath == str(first)
    assert root.property("pageState") == "editor"
    assert not playback.playing
    expected = load_project_document(first).state.content
    second = prepare_study(engine, editor, tmp_path, "weave")
    assert second != first and first.read_bytes() == before
    assert editor.state.content == expected
    assert not playback.playing
    appearance = engine.rootContext().contextProperty("studioAppearance")
    assert appearance.values["feedbackStyle"] == "weave"
    exports = []
    for style in ("string", "weave", "legacy"):
        appearance.setValue("feedbackStyle", style)
        appearance.setValue("feedbackLevel", "off" if style == "legacy" else "standard")
        target = tmp_path / (style + ".mid")
        editor.exportMidi(str(target))
        assert target.is_file()
        exports.append(target.read_bytes())
    assert exports[0] == exports[1] == exports[2]
    assert editor.state.content == expected


def test_styles_draw_distinct_real_pcm_and_clear_without_mutating_music(qapp, rig):
    mixer, player, playback, feedback = rig
    tap = PcmSnapshotTap()
    mixer.set_visualizer_tap(tap)
    visualizer = AudioVisualizerController(tap, auto_start=False)
    engine, root, effect = load_feedback(qapp, playback, visualizer, feedback)
    window = QQuickWindow()
    window.resize(900, 480)
    root.setParentItem(window.contentItem())
    config = root.findChild(QObject, "feedbackSettings")
    original = playback._editor.state
    history = playback._editor._history.undo_depth
    images = []
    try:
        window.show()
        player.play_notes((note("actual", duration=3),))
        advance(rig, 0.02)
        visualizer.process_once()
        for style in ("string", "weave"):
            config.setProperty("values", {"feedbackStyle": style, "feedbackLevel": "standard",
                "feedbackStrength": "balanced", "noteFeedback": True, "keyFeedback": True,
                "spectrum": True, "atmosphere": True, "glassWidth": 1, "glassPositionX": 0.5})
            QTest.qWait(60)
            images.append(window.grabWindow())
            assert effect.property("style") == style
            assert effect.property("enabled") is False
            pcm = root.findChild(QObject, "studioPcmFeedback")
            assert pcm.width() == 828 and pcm.height() <= 88
            assert effect.property("displayRms") > 0
        assert images[0].copy(QRect(88, 370, 700, 100)) != images[1].copy(QRect(88, 370, 700, 100))
        playback.setVolume(0)
        QTest.qWait(60)
        assert effect.property("displayRms") == 0
        assert effect.property("analysisRequested") is False
        assert images[1].copy(QRect(88, 370, 700, 100)) != window.grabWindow().copy(QRect(88, 370, 700, 100))
        config.setProperty("values", {"feedbackLevel": "off"})
        QTest.qWait(30)
        assert effect.property("animationRunning") is False
        assert feedback.voices == []
        assert playback._editor.state == original
        assert playback._editor._history.undo_depth == history
    finally:
        visualizer.shutdown()
        window.close()
        root.setParentItem(None)
        root.deleteLater()
        window.deleteLater()
        engine.deleteLater()
        qapp.processEvents()
