"""Capture real offscreen Qt interaction, with PCM consumed in memory, not speakers.

Run with the isolated development Python. This is an evidence harness, never a
production backend; only the hardware sink methods are replaced. PNG frames are
captured directly from QQuickWindow. No UI compositing or fabricated spectrum.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QBuffer, QIODevice, QObject, QPointF, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.studio import build_studio_engine


def no_device_start(self, *_args, **_kwargs):
    assert self._sink is None, "Memory capture unexpectedly owns a hardware sink"
    self._set_audio_ready_locked(True)


def no_device_pause(self):
    self._set_audio_ready_locked(False)


def install_memory_output(patch=None):
    """Seal public AND internal restart paths; fail closed at hardware creation.

    ``patch`` accepts pytest's monkeypatch.setattr for scoped regression tests.
    The returned list must remain empty, even if a caller catches an exception.
    """
    from ai_piano_studio.audio import mixer as mixer_module
    from ai_piano_studio.audio.player import AudioPlayer
    from PySide6.QtMultimedia import QMediaPlayer
    assign = patch or setattr
    blocked = []
    def reject_sink(*_args, **_kwargs):
        blocked.append("QAudioSink construction")
        raise AssertionError("Hardware audio is forbidden in memory capture")
    def reject_media_play(*_args, **_kwargs):
        blocked.append("QMediaPlayer playback")
        raise AssertionError("Fallback hardware playback is forbidden in memory capture")
    assign(mixer_module, "QAudioSink", reject_sink)
    assign(QMediaPlayer, "play", reject_media_play)
    # A memory sink has no default physical device to rebind after hotplug.
    assign(AudioPlayer, "_bind_default_output", lambda self, **kwargs: None)
    assign(PianoVoiceMixer, "start_audio", no_device_start)
    assign(PianoVoiceMixer, "_start_audio_locked", no_device_start)
    assign(PianoVoiceMixer, "resume_audio", no_device_start)
    assign(PianoVoiceMixer, "pause_audio", no_device_pause)
    assign(PianoVoiceMixer, "processed_audio_seconds", lambda self: getattr(self, "_capture_consumed", 0.0))
    assign(PianoVoiceMixer, "output_latency_seconds", lambda self: 0.0)
    return blocked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "docs/rebuild/evidence")
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--dpi-check", action="store_true")
    parser.add_argument("--review-feedback", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_paths = (
        "src/ai_piano_studio/studio.py",
        "src/ai_piano_studio/qml/Main.qml",
        "src/ai_piano_studio/qml/StudioHome.qml",
        "src/ai_piano_studio/qml/StudioTransportBar.qml",
        "src/ai_piano_studio/qml/StudioCommandBar.qml",
        "src/ai_piano_studio/qml/WorkspaceShell.qml",
        "src/ai_piano_studio/qml/WelcomeLibraryView.qml",
        "src/ai_piano_studio/qml/AssistantWindow.qml",
        "src/ai_piano_studio/qml/Timeline.qml",
        "src/ai_piano_studio/qml/Theme.qml",
        "tools/studio_evidence.py",
    )
    provenance = {
        "sourceCommit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, encoding="utf-8"
        ).strip(),
        "sourceSha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in source_paths
        },
    }
    hardware_calls = install_memory_output()
    app = QApplication([])
    with tempfile.TemporaryDirectory(prefix="studio-evidence-") as temp:
        app, engine, editor, playback, keyboard = build_studio_engine(Path(temp))
        root = engine.rootObjects()[0]
        root.resize(1440, 900)
        initialize_deferred_services(engine)
        log = []

        def settle(ms=80):
            QTest.qWait(ms)

        def find(name):
            result = root.findChild(QObject, name)
            assert result is not None, name
            return result

        def click(name):
            item = find(name)
            assert item.isVisible() and item.isEnabled(), name
            p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
            assert 0 <= p.x() < root.width() and 0 <= p.y() < root.height(), (name, p)
            QTest.mouseClick(root, Qt.LeftButton, pos=p)
            log.append({"action": name, "notes": len(editor.state.notes), "page": root.property("pageState")})

        def shot(name):
            settle()
            if args.review_feedback:
                # Let normal success notifications expire before layout review.
                feedback = find("feedbackHost")
                deadline = time.perf_counter() + 6
                while feedback.isVisible() and time.perf_counter() < deadline:
                    settle(20)
                assert not feedback.isVisible(), "Unexpected persistent feedback during capture"
            assert root.grabWindow().save(str(output / f"{name}.png"))

        settle()
        if args.dpi_check:
            root.resize(1000, 640)
            shot("home-1000")
            click("studioCreateButton")
            settle()
            canvas = find("drawNoteArea")
            for count, x in enumerate((180, 260, 340), 1):
                point = canvas.mapToScene(QPointF(x, 180)).toPoint()
                QTest.mouseClick(root, Qt.LeftButton, pos=point)
                assert len(editor.state.notes) == count
            shot("editor-1000")
            (output / "dpi.json").write_text(json.dumps({**provenance, "logicalSize": [1000,640], "dpr": root.devicePixelRatio(), "physicalCapture": [root.grabWindow().width(),root.grabWindow().height()], "actualCanvasClicks": 3, "notesCreated": len(editor.state.notes), "platform": "offscreen; not native monitor DPI"}, indent=2), encoding="utf-8")
            playback.stop()
            root.close()
            engine.deleteLater()
            app.processEvents()
            return
        shot("home-empty-ink")
        for index, title in enumerate(("午后的练习", "雨滴与和弦", "写给明天")):
            editor.newProject()
            for n in range(12):
                editor.createNoteAt(n * 40, (n % 5 + index) * 18 + 160)
            assert editor.saveNamedProject(title)
            while editor.isSaving:
                settle(10)
        editor.newProject()
        shot("home-recent-ink")
        if args.review_feedback:
            from PySide6.QtQml import QQmlEngine, QQmlExpression
            click("studioCreateButton")
            settle()
            canvas = find("drawNoteArea")
            QTest.mouseClick(root, Qt.LeftButton, pos=canvas.mapToScene(QPointF(180, 180)).toPoint())
            click("studioViewMenuButton")
            settle()
            menu = find("studioViewMenu")
            expression = QQmlExpression(QQmlEngine.contextForObject(menu), menu, "itemAt(1)")
            item, undefined = expression.evaluate()
            assert not expression.hasError() and not undefined
            QTest.mouseClick(root, Qt.LeftButton, pos=item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint())
            settle()
            assert root.property("focusMode")
            click("studioArrangement")
            click("studioInspector")
            shot("focus-panels")
            assert find("arrangementPanel").isVisible() and find("inspector").isVisible()
            assert editor.saveNamedProject("声部练习")
            while editor.isSaving:
                settle(10)
            click("studioHomeNav")
            settle()
            click("studioLibraryButton")
            root.resize(2200, 900)
            shot("library-wide")
            assert not find("libraryScrollBar").isVisible()
            click("libraryBackButton")
            root.resize(1000, 640)
            shot("home-1000")
            (output / "capture-log.json").write_text(json.dumps({**provenance,
                "environment": "Qt offscreen software; temporary profile; no audio device",
                "actions": log, "focusPanelsVisible": True, "fittingLibraryScrollBarHidden": True,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            playback.stop()
            root.close()
            engine.deleteLater()
            app.processEvents()
            return
        click("studioCreateButton")
        settle()
        shot("editor-empty")
        templates = engine.rootContext().contextProperty("templates")
        templates.adapt(templates.templates[0]["id"])
        settle(200)
        root.setPage("editor", False)
        shot("editor-score")
        experience = engine.rootContext().contextProperty("experience")
        experience.setArrangementVisible(True)
        shot("editor-arrangement")
        click("studioInspector")
        shot("editor-inspector")
        click("studioInspector")
        click("studioAssistant")
        settle(150)
        assistant_window = find("assistantWindow")
        assert assistant_window.grabWindow().save(str(output / "assistant.png"))
        assistant_window.close()
        root.openAppearancePopup(find("studioViewMenuButton"))
        settle()
        shot("appearance-theme")
        appearance_dialog = find("studioAppearanceDialog")
        appearance_dialog.setProperty("page", 1)
        shot("appearance-background")
        appearance = engine.rootContext().contextProperty("studioAppearance")
        appearance.setValue("themeId", "paper")
        shot("appearance-paper")
        click("studioAppearanceApply")
        shot("editor-paper")
        root.resize(1000, 640)
        shot("editor-paper-1000")
        root.setPage("launch", False)
        shot("home-paper-1000")
        root.resize(1440, 900)
        appearance.beginPreview()
        appearance.setValue("themeId", "ink")
        appearance.applyPreview()
        editor.newProject()
        root.setPage("launch", False)
        settle()

        if args.video:
            # Real mixer/tone source/transport/FFT remain intact. Consume their
            # PCM as a silent memory sink at wall-clock rate, never QAudioSink.
            mixer = playback.piano_source.mixer
            last_audio = time.perf_counter()
            pcm_peak = [0]
            import numpy as np

            def consume_audio():
                nonlocal last_audio
                now = time.perf_counter()
                frames = int((now - last_audio) * mixer._sample_rate)
                last_audio = now
                if mixer._audio_ready and frames > 0:
                    mixer._capture_consumed = getattr(mixer, "_capture_consumed", 0) + frames / mixer._sample_rate
                    payload = mixer._read_output_frames(frames)
                    if payload:
                        pcm_peak[0] = max(pcm_peak[0], int(np.abs(np.frombuffer(payload, dtype=np.int16).astype(np.int32)).max()))

            audio_timer = QTimer()
            audio_timer.setInterval(10)
            audio_timer.timeout.connect(consume_audio)
            audio_timer.start()
            playback.setVolume(0.65)
            actions = []

            def add(at, callback):
                actions.append((at, callback))

            add(1.5, lambda: click("studioCreateButton"))

            def write_note(index):
                area = find("drawNoteArea")
                p = area.mapToScene(QPointF(90 + index * 68, 180 + (index % 4) * 18))
                QTest.mouseClick(root, Qt.LeftButton, pos=p.toPoint())
                assert len(editor.state.notes) == index + 1

            for n in range(8):
                add(3 + n * 0.5, lambda n=n: write_note(n))

            def drag_note():
                area = find("drawNoteArea")
                p = area.mapToScene(QPointF(90 + 7 * 68 + 8, 180 + 3 * 18 + 8))
                QTest.mousePress(root, Qt.LeftButton, pos=p.toPoint())
                QTest.mouseMove(root, (p + QPointF(40, -36)).toPoint(), delay=30)
                QTest.mouseRelease(root, Qt.LeftButton, pos=(p + QPointF(40, -36)).toPoint())

            add(7.5, drag_note)
            add(9, lambda: click("studioPlayPauseButton"))
            add(9.5, lambda: click("syncAndPlayAction") if find("unsyncedPlaybackDialog").property("opened") else None)
            add(13.5, lambda: click("studioStopButton"))
            add(14.5, lambda: root.openAppearancePopup(find("studioViewMenuButton")))
            add(16, lambda: appearance.setValue("themeId", "paper"))
            add(17, lambda: find("studioAppearanceDialog").setProperty("page", 1))
            add(18, lambda: appearance.setValue("editorOpacity", 0.5))
            add(19, lambda: find("studioAppearanceDialog").setProperty("page", 3))
            add(20, lambda: appearance.setValue("reducedMotion", True))
            add(21, lambda: click("studioAppearanceApply"))
            add(22, lambda: click("studioSave"))

            def save_demo():
                # Name dialog is the same production save route invoked above.
                dialog = find("projectNameDialog")
                name_field = find("projectNameField")
                name_field.setProperty("text", "八个音符的开始")
                click("projectNameConfirmButton")

            add(23.5, save_demo)
            add(26, lambda: click("studioHomeNav"))
            frame_times = []
            fps = 15
            video = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", str(fps), "-i", "-", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24", str(output / "studio-demo.mp4")], stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            start = time.perf_counter()
            try:
                for frame in range(30 * fps):
                    target = frame / fps
                    while time.perf_counter() - start < target:
                        settle(5)
                    elapsed = time.perf_counter() - start
                    while actions and elapsed >= actions[0][0]:
                        at, action = actions.pop(0)
                        action()
                        log.append({"time": elapsed, "scheduled": at, "notes": len(editor.state.notes), "dirty": editor.isDirty})
                    app.processEvents()
                    buffer = QBuffer()
                    buffer.open(QIODevice.WriteOnly)
                    assert root.grabWindow().save(buffer, "PNG")
                    video.stdin.write(bytes(buffer.data()))
                    frame_times.append(time.perf_counter() - start)
                video.stdin.close()
                assert video.wait(timeout=30) == 0
                assert pcm_peak[0] > 0, "Real mixer never produced nonzero PCM"
                assert editor.currentFilePath and not editor.isDirty, "Demo save did not finish"
                import shutil
                shutil.copy2(editor.currentFilePath, output / "demo-project.pianoai.json")
                log.append({"pcmPeak": pcm_peak[0], "captureFps": fps, "frameTimes": frame_times, "savedFile": Path(editor.currentFilePath).name})
            finally:
                audio_timer.stop()
                playback.stop()
                if video.poll() is None:
                    video.terminate()

        assert not hardware_calls, "Hardware audio guard was reached"
        (output / "capture-log.json").write_text(json.dumps({**provenance, "hardwareCalls": hardware_calls, "environment": "Windows / Qt offscreen software / memory PCM sink (no audio device)", "logicalSize": [1440,900], "dpr": root.devicePixelRatio(), "actions": log}, ensure_ascii=False, indent=2), encoding="utf-8")
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    main()
