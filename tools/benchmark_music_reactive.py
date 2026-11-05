"""Paired software-render/input costs, actual in-memory PCM, no desktop IO."""
from __future__ import annotations

import argparse
import cProfile
import io
import pstats
from array import array
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import tempfile
import time

os.environ["QSG_RENDER_LOOP"] = "basic"
from studio_evidence import install_memory_output, ROOT
from benchmark_studio import statistics_ms, visual_item
from PySide6.QtCore import QPointF, Qt, QMetaObject, qVersion
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.studio import build_studio_engine
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts", type=int, nargs="+", default=[100, 500, 1000, 2000, 3000, 5000])
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--detailed", action="store_true", help="Only after a concrete quick-check failure")
    parser.add_argument("--profile-create", action="store_true", help="Diagnose one observed slow input; excludes timing claims")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/rebuild/music-reactive/performance.json")
    args = parser.parse_args()
    hardware_calls = install_memory_output()
    app = QApplication([])
    configurations = [("off", "balanced")] + [(s, v) for s in ("string", "weave")
        for v in (("gentle", "balanced", "vivid") if args.detailed else ("balanced",))]
    warmups = 3 if args.detailed else 0
    results = []
    with tempfile.TemporaryDirectory(prefix="music-perf-") as directory:
        app, engine, editor, playback, keyboard = build_studio_engine(Path(directory))
        root = engine.rootObjects()[0]
        root.resize(1440, 900)
        initialize_deferred_services(engine)
        root.setProperty("pageState", "editor")
        appearance = engine.rootContext().contextProperty("studioAppearance")
        feedback = engine.rootContext().contextProperty("studioFeedback")
        experience = engine.rootContext().contextProperty("experience")
        experience.setMiniMapVisible(True)
        playback.setVolume(0.65)
        mixer = playback._player._mixer
        selection = playback.piano_source._coordinator.selection()
        if selection is not None:
            for bank in (selection.primary, selection.secondary):
                if bank is not None:
                    roots = {bank.nearest_root(p) for p in list(range(48, 72)) + [81]}
                    assert bank.preload_roots_sync(sorted(roots))
        for count in args.counts:
            initial = ProjectState(tuple(Note.create(id=f"measure-{i}", pitch=48 + i % 24,
                start_time=i * .04, duration=.48, velocity=80, status="saved") for i in range(count)))
            buckets = {(s, v): [] for s, v in configurations}
            for sample in range(args.samples + warmups):
                # Rotate order per paired sample to reduce warm-up/order bias.
                offset = sample % len(configurations)
                for style, level in configurations[offset:] + configurations[:offset]:
                    playback.stop()
                    editor._replace_project(initial, saved_content=initial.content, path=None)
                    root.setProperty("pageState", "editor")
                    appearance.setValue("feedbackStyle", "string" if style == "off" else style)
                    appearance.setValue("feedbackStrength", level)
                    appearance.setValue("feedbackLevel", "off" if style == "off" else "standard")
                    app.processEvents()
                    timeline = visual_item(root.contentItem(), "timeline")
                    if timeline is not None:
                        timeline.setProperty("followPlayback", False)
                        timeline.setProperty("viewStartSeconds", 0)
                    mixer._capture_consumed = 0.0
                    playback.play()
                    session = playback._player._project_session
                    assert session is not None and playback.playing
                    pcm_times, peak = [], 0
                    for step in range(1, 21):
                        mixer._capture_consumed = (step - 1) * .01
                        session.process_due_events()
                        start = time.perf_counter()
                        pcm = mixer._read_output_frames(480)
                        pcm_times.append((time.perf_counter() - start) * 1000)
                        values = array("h", pcm)
                        peak = max(peak, max(map(abs, values), default=0))
                    mixer._capture_consumed = .2
                    session.process_due_events()
                    app.processEvents()
                    QTest.qWait(40)  # Let the existing FFT worker deliver, outside timings.
                    effect = visual_item(root.contentItem(), "studioFeedback")
                    assert effect is not None
                    if style != "off":
                        feedback.sample()
                        assert effect.property("displayRms") > 0
                    assert peak > 0
                    root.grabWindow()
                    row = {"sample": sample - warmups, "pcm_peak": peak,
                        "pcm_10ms_chunk_max_ms": max(pcm_times), "pcm_10ms_chunk_p50_ms": statistics.median(pcm_times)}

                    def measured(name, action):
                        submissions = []
                        render_started, render_costs = [], []
                        def rendering():
                            render_started.append(time.perf_counter())
                        def rendered():
                            finished = time.perf_counter()
                            submissions.append(finished)
                            if render_started:
                                render_costs.append((finished-render_started[-1])*1000)
                        root.beforeRendering.connect(rendering, Qt.DirectConnection)
                        root.afterRendering.connect(rendered, Qt.DirectConnection)
                        profiler = cProfile.Profile() if args.profile_create and name == "create" else None
                        if profiler:
                            profiler.enable()
                        start = time.perf_counter()
                        action()
                        dispatched = time.perf_counter()
                        app.processEvents()
                        frame = root.grabWindow()
                        end = time.perf_counter()
                        if profiler:
                            profiler.disable()
                            stream = io.StringIO()
                            pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(35)
                            args.output.parent.mkdir(parents=True, exist_ok=True)
                            args.output.with_name(f"profile-create-{count}-{style}.txt").write_text(stream.getvalue(), encoding="utf-8")
                        root.afterRendering.disconnect(rendered)
                        root.beforeRendering.disconnect(rendering)
                        assert not frame.isNull()
                        row[name + "_ms"] = (end - start) * 1000
                        row[name + "_dispatch_ms"] = (dispatched - start) * 1000
                        row[name + "_render_submissions"] = len(submissions)
                        row[name + "_software_render_ms"] = sum(render_costs)
                        return frame

                    pointer = visual_item(root.contentItem(), "drawNoteArea")
                    assert pointer is not None and pointer.isVisible() and pointer.isEnabled()
                    point = pointer.mapToScene(QPointF(260, 9)).toPoint()
                    before = root.grabWindow()
                    history = editor._history.undo_depth
                    after = measured("create", lambda: QTest.mouseClick(root, Qt.LeftButton, pos=point))
                    assert len(editor.state.notes) == count + 1
                    assert editor._history.undo_depth == history + 1
                    assert before.copy(point.x() + 3, point.y() - 4, 14, 8) != after.copy(point.x() + 3, point.y() - 4, 14, 8)
                    editor.undo()
                    assert editor.state.content == initial.content
                    if not args.detailed:
                        playback.stop()
                        app.processEvents()
                        assert mixer._sink is None and not hardware_calls
                        buckets[(style, level)].append(row)
                        continue
                    ids = [n.id for n in initial.notes[:100]]
                    measured("select100", lambda: editor.selectNotes(ids))
                    assert editor.selectedCount == 100
                    def drag():
                        editor.beginGesture()
                        editor.previewMovePixels(20, 0)
                        editor.commitGesture()
                    measured("drag100", drag)
                    assert sum(a != b for a, b in zip(initial.notes, editor.state.notes)) == 100
                    editor.undo()
                    assert editor.state.content == initial.content
                    measured("layer_color", lambda: editor.setMusicLayerProperty(initial.layers[0].id, "customColor", "#B080C0"))
                    assert editor.state.layers[0].custom_color == "#B080C0"
                    editor.undo()
                    assert editor.state.content == initial.content
                    button = visual_item(root.contentItem(), "studioArrangement")
                    def arrangement():
                        pos = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
                        QTest.mouseClick(root, Qt.LeftButton, pos=pos)
                    measured("arrangement_open", arrangement)
                    arrangement()
                    app.processEvents()
                    measured("feedback_frame", lambda: QMetaObject.invokeMethod(effect, "repaint"))
                    measured("pause", playback.pause)
                    assert not playback.playing
                    measured("resume", playback.play)
                    assert playback.playing
                    playback.stop()
                    app.processEvents()
                    assert not feedback.voices
                    assert editor.state.content == initial.content
                    if sample >= warmups:
                        buckets[(style, level)].append(row)
                print(json.dumps({"notes": count, "paired_sample": sample - warmups + 1}), flush=True)
            for (style, level), rows in buckets.items():
                results.append({"notes": count, "style": style, "strength": level, "samples": rows,
                    "summary": {key: statistics_ms([r[key] for r in rows])
                        for key in rows[0] if key.endswith("_ms")}})
            payload = {"environment": {"qt": qVersion(), "platform": platform.platform(),
                "renderer": str(root.rendererInterface().graphicsApi()), "window": [1440, 900],
                "dpr": root.devicePixelRatio(), "volume": playback.volume, "overview": "visible"},
                "scope": "Quick checks by default: one actual Qt create and Undo per requested size/style. Detailed mode is opt-in after a finding. CPU dispatch + software grab completion, not physical input/display latency. PCM is consumed before each measurement; hardware is blocked at sink construction and internal restart. No hardware continuity claim. A single sample is not a percentile estimate.",
                "hardware_calls": hardware_calls, "detailed": args.detailed, "warmups": warmups,
                "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in [Path(__file__).resolve(), ROOT/'src/ai_piano_studio/qml/StudioFeedback.qml',
                        ROOT/'tools/studio_evidence.py', ROOT/'src/ai_piano_studio/qml/MiniMap.qml',
                        ROOT/'src/ai_piano_studio/editor/composite.py',
                        ROOT/'src/ai_piano_studio/audio/studio_feedback.py', ROOT/'src/ai_piano_studio/audio/player.py']},
                "results": results}
            assert mixer._sink is None and not hardware_calls
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    main()
