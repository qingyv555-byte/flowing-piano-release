"""Bounded comparative Qt probe; simulated input/voices, no desktop or audio access.

Run from the repository root with PYTHONPATH=src. Tracemalloc is enabled after
imports; its snapshots have overhead. Compare like stages, not to old soak RSS.
"""
import argparse
import gc
import json
from pathlib import Path
import tempfile
import time
import tracemalloc

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings, QTimer
from measure_creative_resources import (
    CreativeController, MemoryCredentialStore, Note, ProjectDocument, ProjectState,
    ProviderSettingsService, SilentMixer, SilentSource, SimulatedActivity,
    resources, save_project_document,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.seconds <= 60:
        parser.error('stage duration must be 2–60 seconds')
    app = QCoreApplication([])
    resources()  # Initialize sampling types before tracing application activity.
    tracemalloc.start(8)
    report = {'seconds_per_stage': args.seconds, 'repeats': 2, 'audio_device_calls': 0,
              'online_calls': 0, 'input': 'simulated', 'stages': []}
    with tempfile.TemporaryDirectory(prefix='creative-profile-') as folder:
        home = Path(folder)
        mixer = SilentMixer()
        source = SilentSource(mixer)
        settings = ProviderSettingsService(settings=QSettings(str(home/'settings.ini'), QSettings.IniFormat), credentials=MemoryCredentialStore())
        controller = CreativeController(home, source, mixer, lambda: False, provider_settings=settings)
        controller._activity = SimulatedActivity()
        controller.open()
        document = ProjectDocument(ProjectState(notes=tuple(Note.create(pitch=60+i, start_time=i*.1, duration=.08) for i in range(8))))
        save_project_document(home/'theme.json', document)
        assert controller.loadTheme(str(home/'theme.json'))
        probe_timer = QTimer()
        probe_timer.setInterval(250)
        probe_timer.timeout.connect(controller.changed.emit)
        activity_timer = QTimer()
        activity_timer.setInterval(250)
        activity_timer.timeout.connect(lambda: controller.activity(200, 1))

        def snapshot():
            children = controller.findChildren(QObject)
            timers = [obj for obj in children if isinstance(obj, QTimer)]
            return {**resources(), 'traced_current': tracemalloc.get_traced_memory()[0],
                    'trace_overhead': tracemalloc.get_tracemalloc_memory(),
                    'qt_children': len(children), 'active_timers': sum(t.isActive() for t in timers),
                    'voices': controller._transport.voice_count,
                    'pending': len(controller._transport._pending),
                    'releases': len(controller._transport._releases),
                    'theme_notes': len(controller._companion._notes),
                    'messages': len(controller.assistant.messages.entries) if controller.assistant else 0,
                    'notes_started': source.started}

        def run_loop(seconds):
            QTimer.singleShot(round(seconds*1000), app.quit)
            app.exec()

        for repeat in range(2):
            for phase in ('idle', 'timer', 'theme', 'activity'):
                controller.pauseCompanion()
                probe_timer.stop()
                activity_timer.stop()
                if phase == 'timer':
                    probe_timer.start()
                if phase in {'theme', 'activity'}:
                    controller.startCompanion()
                if phase == 'activity':
                    activity_timer.start()
                gc.collect()
                before = tracemalloc.take_snapshot()
                start = snapshot()
                started = time.monotonic()
                run_loop(args.seconds)
                end = snapshot()
                after = tracemalloc.take_snapshot()
                top = [{'location': str(item.traceback), 'bytes': item.size_diff, 'count': item.count_diff}
                       for item in after.compare_to(before, 'lineno')[:8]]
                del before, after
                report['stages'].append({'repeat': repeat, 'phase': phase, 'elapsed': time.monotonic()-started,
                                         'start': start, 'end': end, 'top': top})
                print(json.dumps({'repeat': repeat, 'phase': phase,
                                  'private_delta': end['private_bytes']-start['private_bytes'],
                                  'python_delta': end['traced_current']-start['traced_current']}), flush=True)
        controller.pauseCompanion()
        probe_timer.stop()
        activity_timer.stop()
        report['before_close'] = snapshot()
        controller.shutdown()
        report['after_shutdown'] = snapshot()
        probe_timer.deleteLater()
        activity_timer.deleteLater()
        controller.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        del controller, settings, source, mixer, probe_timer, activity_timer
        gc.collect()
        run_loop(2)
        report['after_delete'] = {**resources(), 'traced_current': tracemalloc.get_traced_memory()[0],
                                  'trace_overhead': tracemalloc.get_tracemalloc_memory()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
