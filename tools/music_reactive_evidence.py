"""Actual Qt study capture; deterministic memory PCM, isolated profile, no device.

--quick emits still/play/paper frames. Default emits a 25 s, 15 fps video.
Only hardware output is replaced. The production scheduler, voices, tap, FFT,
settings and editor remain operative. Recorded cadence is not native FPS.
"""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import wave

os.environ["QSG_RENDER_LOOP"] = "basic"
from studio_evidence import ROOT, install_memory_output
from benchmark_studio import visual_item
from PySide6.QtCore import QBuffer, QIODevice, QObject, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.studio import build_studio_engine
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.persistence.project_document import ProjectDocument, save_project_document


def fixture():
    """Original C-major study; fixed ids/timestamps make serialization stable."""
    notes = []
    def add(label, pitch, start, duration, velocity=80, layer="layer-default"):
        notes.append(Note.create(id=label, pitch=pitch, start_time=start,
            duration=duration, velocity=velocity, layer_id=layer, status="saved",
            created_at="2026-09-13T00:00:00.000+00:00",
            updated_at="2026-09-13T00:00:00.000+00:00"))
    add("single", 72, 1, .38)
    for i in range(5):
        add(f"repeat-{i+1}", 72, 3 + i * .4, .18, 66 + i * 7)
    add("long", 76, 6, 3, 86)
    for i, pitch in enumerate((60, 64, 67, 72)):
        add(f"chord-{i}", pitch, 10, 1.2, 74, "accompaniment" if i < 3 else "layer-default")
    for i in range(6):
        add(f"composite-{i}", 74, 12 + i * .35, .35, 75 + i * 2)
    for i in range(24):
        add(f"arpeggio-{i}", (60, 64, 67, 72, 76, 79)[i % 6],
            15 + i * .2, .3, 55 + i * 2)
    for i in range(8):
        for j, pitch in enumerate((48, 55, 60)):
            add(f"accompaniment-{i}-{j}", pitch, 15 + i * .6, .48,
                48 + i * 5, "accompaniment")
    return ProjectState(tuple(notes), layers=(
        MusicLayer.create(id="layer-default", name="旋律 · 弦与织", role="melody", order=0),
        MusicLayer.create(id="accompaniment", name="和声 · 呼吸", role="accompaniment", order=1)))


def verify_pair(output):
    """Compare score playback exactly; retain wall-timed editing auditions.

    The existing audition release uses QTimer.singleShot (wall time), whereas
    score playback uses consumed PCM time. Do not rewrite either clock or audio
    to force editing tails from two offscreen recordings to have equal bytes.
    """
    logs = [json.loads((output / f"{style}-capture.json").read_text(encoding="utf-8"))
            for style in ("string", "weave")]
    assert logs[0]["fixtureSha256"] == logs[1]["fixtureSha256"]
    assert all(log["hardwareCalls"] == [] for log in logs)
    prefixes = []
    for style in ("string", "weave"):
        with wave.open(str(output / f"{style}.wav")) as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (2, 2, 48000, 1200000)
            prefixes.append(wav.readframes(22 * 48000))
    assert prefixes[0] == prefixes[1], "A/B score playback PCM differs before editing"
    result = {"scorePlaybackSeconds": 22, "scorePcmSha256": hashlib.sha256(prefixes[0]).hexdigest(),
              "wholePcmIdentical": logs[0]["pcmSha256"] == logs[1]["pcmSha256"],
              "scope": "0-22s score PCM is byte-identical. 22-25s retains actual edits and wall-timed audition release; no audio replacement or clock override.",
              "hardwareCalls": [log["hardwareCalls"] for log in logs]}
    (output / "pair-validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--style", choices=("string", "weave", "glass", "both"), default="both")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/rebuild/music-reactive/evidence")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    state = fixture()
    fixture_path = args.output / "original-study.pianoai.json"
    save_project_document(fixture_path, ProjectDocument(state))
    save_project_document(ROOT / "src/ai_piano_studio/resources/music-reactive-study.pianoai.json", ProjectDocument(state))
    if args.style == "both":
        import sys
        for style in ("string", "weave"):
            subprocess.run([sys.executable, __file__, "--style", style, "--output", str(args.output)]
                + (["--quick"] if args.quick else []), check=True)
        if not args.quick:
            verify_pair(args.output)
        return
    hardware_calls = install_memory_output()
    app = QApplication([])
    with tempfile.TemporaryDirectory(prefix="music-study-") as profile:
        app, engine, editor, playback, keyboard = build_studio_engine(Path(profile))
        root = engine.rootObjects()[0]
        root.resize(1440, 900)
        initialize_deferred_services(engine)
        editor._replace_project(state, saved_content=state.content, path=None)
        root.setProperty("pageState", "editor")
        appearance = engine.rootContext().contextProperty("studioAppearance")
        appearance.beginPreview()
        for name, value in {"feedbackStyle": args.style, "feedbackStrength": "balanced",
                "feedbackLevel": "standard", "themeId": "ink"}.items():
            appearance.setValue(name, value)
        appearance.applyPreview()
        QTest.qWait(100)
        def find(name):
            item = visual_item(root.contentItem(), name)
            assert item is not None, name
            return item
        timeline = find("timeline")
        timeline.setProperty("pixelsPerSecond", 45)
        timeline.setProperty("followPlayback", False)
        timeline.setProperty("viewportTop", 81)
        editor.setViewScale(45, 18)
        QTest.qWait(80)
        def shot(label):
            image = root.grabWindow()
            assert not image.isNull()
            assert image.save(str(args.output / f"{args.style}-{label}.png"))
            return image
        shot("still")
        mixer = playback.piano_source.mixer
        selection = playback.piano_source._coordinator.selection()
        warmed_banks = []
        if selection is not None:
            for bank in (selection.primary, selection.secondary):
                if bank is not None:
                    roots = sorted({bank.nearest_root(note.pitch) for note in state.notes})
                    assert bank.preload_roots_sync(roots), "Study sample preload failed"
                    warmed_banks.append(roots)
        mixer._capture_consumed = 0.0
        playback.setVolume(.65)
        playback.setDragPreviewMode("off")
        actions, frames, pcm_log, voice_log = [], [], [], []
        payloads = []
        consumed_frames = 0
        def click(name):
            item = find(name)
            assert item.isVisible() and item.isEnabled(), name
            p = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
            QTest.mouseClick(root, Qt.LeftButton, pos=p)
        click("studioPlayPauseButton")
        QTest.qWait(40)
        overlap = root.findChild(QObject, "overlapPlaybackDialog")
        if overlap is not None and overlap.property("opened"):
            click("overlapPlayAction")
        deadline = time.perf_counter() + 3
        while not playback.playing and time.perf_counter() < deadline:
            QTest.qWait(10)
        assert playback.playing, "Real play action did not start"
        session = playback._player._project_session
        assert session is not None
        # Deterministic 10 ms output pulls, with normal Qt timers also running.
        # The production session observes this consumed-sample clock, never a
        # second visual clock. Output PCM is scaled exactly as QAudioSink volume.
        def advance(target):
            nonlocal consumed_frames
            target_frame = round(target * mixer._sample_rate)
            while consumed_frames < target_frame:
                session.process_due_events()
                count = min(480, target_frame - consumed_frames)
                start = time.perf_counter()
                data = mixer._read_output_frames(count)
                samples = array("h", data)
                scaled = array("h", (round(v * playback.volume) for v in samples))
                payloads.append(scaled.tobytes())
                consumed_frames += count
                mixer._capture_consumed = consumed_frames / mixer._sample_rate
                pcm_log.append({"endSeconds": mixer._capture_consumed,
                    "frames": count, "mixMs": (time.perf_counter()-start)*1000,
                    "peak": max(map(abs, scaled), default=0)})
                app.processEvents()
            session.process_due_events()
        feedback = engine.rootContext().contextProperty("studioFeedback")
        if args.quick:
            advance(1.1)
            QTest.qWait(80)
            shot("single")
            advance(16.1)
            QTest.qWait(80)
            shot("play")
            appearance.beginPreview()
            appearance.setValue("themeId", "paper")
            appearance.applyPreview()
            QTest.qWait(80)
            shot("paper-play")
            playback.stop()
            QTest.qWait(80)
            shot("paper-still")
        else:
            fps = 15
            silent = args.output / f"{args.style}-silent.mp4"
            video = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "image2pipe", "-framerate", str(fps), "-i", "-", "-an", "-c:v", "libx264",
                "-threads", "2", "-pix_fmt", "yuv420p", "-crf", "20", str(silent)],
                stdin=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            started = time.perf_counter()
            edited = None
            try:
                for frame in range(25 * fps):
                    target = frame / fps
                    advance(target)
                    while time.perf_counter() - started < target:
                        QTest.qWait(2)
                    if frame == 22 * fps:
                        area = find("drawNoteArea")
                        p = area.mapToScene(QPointF(220, 90)).toPoint()
                        count = len(editor.state.notes)
                        QTest.mouseClick(root, Qt.LeftButton, pos=p)
                        assert len(editor.state.notes) == count + 1
                        edited = editor.state.notes[-1]
                        actions.append({"at": target, "action": "QTest create", "noteId": edited.id})
                    if frame == 23 * fps:
                        assert edited is not None
                        item = find("noteItem-" + edited.id)
                        p = item.mapToScene(QPointF(8, item.height()/2)).toPoint()
                        QTest.mousePress(root, Qt.LeftButton, pos=p)
                        QTest.mouseMove(root, p + QPointF(45, -18).toPoint(), delay=10)
                        QTest.mouseRelease(root, Qt.LeftButton, pos=p + QPointF(45, -18).toPoint())
                        moved = next(n for n in editor.state.notes if n.id == edited.id)
                        assert (moved.pitch, moved.start_time) != (edited.pitch, edited.start_time)
                        actions.append({"at": target, "action": "QTest drag", "pitch": moved.pitch})
                    if frame == 24 * fps:
                        editor.undo()
                        editor.undo()
                        assert editor.state.content == state.content
                        actions.append({"at": target, "action": "Undo twice; fixture restored"})
                    app.processEvents()
                    grab_start = time.perf_counter()
                    image = root.grabWindow()
                    if frame == 16 * fps + 1:
                        assert image.save(str(args.output / f"{args.style}-play.png"))
                    buffer = QBuffer()
                    buffer.open(QIODevice.WriteOnly)
                    assert image.save(buffer, "PNG")
                    video.stdin.write(bytes(buffer.data()))
                    frames.append({"frame": frame, "musicSeconds": target,
                        "wallSeconds": time.perf_counter()-started,
                        "grabEncodeMs": (time.perf_counter()-grab_start)*1000})
                    voice_log.append({"seconds": target, "voices": feedback.voices})
                advance(25)
                video.stdin.close()
                assert video.wait(timeout=30) == 0
            finally:
                if video.poll() is None:
                    video.terminate()
            data = b"".join(payloads)
            wav_path = args.output / f"{args.style}.wav"
            with wave.open(str(wav_path), "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(2)
                wav.setframerate(mixer._sample_rate)
                wav.writeframes(data)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent),
                "-i", str(wav_path), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", "25",
                str(args.output / f"{args.style}-study.mp4")], check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            silent.unlink()
            playback.stop()
            appearance.beginPreview()
            appearance.setValue("themeId", "paper")
            appearance.applyPreview()
            playback.seek(16)
            playback.play()
            session = playback._player._project_session
            advance(25.1)
            QTest.qWait(80)
            shot("paper-play")
            playback.stop()
            QTest.qWait(80)
            shot("paper-still")
        assert mixer._sink is None and not hardware_calls, "Hardware audio guard was reached"
        capture_data = b"".join(payloads[:]) if args.quick else data
        assert any(row["peak"] > 0 for row in pcm_log), "No real PCM"
        log = {"style": args.style, "quick": args.quick, "fixtureSha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
            "pcmSha256": hashlib.sha256(capture_data).hexdigest(), "sampleRate": mixer._sample_rate,
            "window": [1440, 900], "dpr": root.devicePixelRatio(), "captureFps": 15,
            "scope": "Qt offscreen software, real scheduler/mixer/FFT, deterministic memory sink; hardware construction/play and internal restart sealed; no physical output/display validation",
            "hardwareCalls": hardware_calls,
            "audio": "post mixer/limiter PCM scaled by application master volume .65; same deterministic sample clock for both studies",
            "underrun": None, "underrunScope": "No hardware sink; mix duration is logged, physical underruns are unobservable",
            "actions": actions, "frames": frames, "pcm": pcm_log, "voices": voice_log,
            "warmedSampleRoots": warmed_banks,
            "sourceSha256": {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in (
                "tools/music_reactive_evidence.py", "tools/studio_evidence.py", "src/ai_piano_studio/qml/StudioFeedback.qml",
                "src/ai_piano_studio/audio/studio_feedback.py", "src/ai_piano_studio/appearance/studio_settings.py")}}
        (args.output / f"{args.style}-capture.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()
        print(str(args.output / f"{args.style}-capture.json"))


if __name__ == "__main__":
    main()
