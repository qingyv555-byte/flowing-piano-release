"""Silent creative scheduler soak. Uses real Qt timers, never an audio device."""
import argparse
import ctypes
from ctypes import wintypes
from functools import cache
import json
from pathlib import Path
import tempfile
import threading
import time

from PySide6.QtCore import QCoreApplication, QObject, QSettings, QTimer, Signal
from ai_piano_studio.creative.controller import CreativeController
from ai_piano_studio.creative.activity import ActivityAccumulator
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
from ai_piano_studio.persistence.project_document import ProjectDocument, save_project_document


class SilentMixer(QObject):
    voiceFinished = Signal(str)
    def start_audio(self, **kwargs):
        return None


class SilentSource:
    def __init__(self, mixer):
        self.mixer, self.started = mixer, 0
    def start_voice(self, *args):
        self.started += 1
    def note_off(self, voice, *args, **kwargs):
        self.mixer.voiceFinished.emit(voice)


class SimulatedActivity(ActivityAccumulator):
    def start(self):
        self.clear()
    def stop(self):
        self.clear()


class ProcessMemoryCounters(ctypes.Structure):
    # PROCESS_MEMORY_COUNTERS_EX; PrivateUsage is committed private memory.
    _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in
        ('peak_working', 'working', 'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]


@cache
def _process_api():
    kernel = ctypes.WinDLL('kernel32')
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    process = kernel.GetCurrentProcess()
    psapi = ctypes.WinDLL('psapi')
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessHandleCount.restype = wintypes.BOOL
    return kernel, psapi, process


def resources():
    kernel, psapi, process = _process_api()
    counters, handles = ProcessMemoryCounters(), wintypes.DWORD()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb) or not kernel.GetProcessHandleCount(process, ctypes.byref(handles)):
        raise RuntimeError('resource sampling failed')
    return {'working_set': counters.working, 'private_bytes': counters.private, 'handles': handles.value,
            'python_threads': threading.active_count(), 'cpu_seconds': time.process_time()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=float, default=1800)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    app = QCoreApplication([])
    with tempfile.TemporaryDirectory(prefix='creative-soak-') as temporary:
        home = Path(temporary)
        settings = ProviderSettingsService(settings=QSettings(str(home/'settings.ini'), QSettings.IniFormat), credentials=MemoryCredentialStore())
        mixer = SilentMixer()
        source = SilentSource(mixer)
        controller = CreativeController(home, source, mixer, lambda: False, provider_settings=settings)
        controller._activity = SimulatedActivity()
        controller.open()
        document = ProjectDocument(ProjectState(notes=tuple(Note.create(pitch=60+i, start_time=i*.5, duration=.2) for i in range(8))))
        path = home/'theme.json'
        save_project_document(path, document)
        assert controller.loadTheme(str(path))
        # Exercise lifecycle without touching input hooks or sound hardware.
        for _ in range(100):
            controller.startCompanion()
            controller.pauseCompanion()
        controller.startCompanion()
        started = time.monotonic()
        samples = []
        def sample():
            elapsed = time.monotonic() - started
            samples.append({'elapsed': elapsed, **resources(), 'voices': controller._transport.voice_count, 'notes_started': source.started})
            controller.activity(200, 1)
            if elapsed >= args.duration:
                controller.shutdown()
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps({'kind': 'silent simulated activity and voices; real Qt scheduling', 'duration': elapsed,
                    'audio_device_calls': 0, 'online_calls': 0, 'lifecycle_cycles': 100, 'samples': samples}, indent=2), encoding='utf-8')
                app.quit()
        timer = QTimer()
        timer.setInterval(10000)
        timer.timeout.connect(sample)
        timer.start()
        sample()
        if args.duration > 0:
            app.exec()


if __name__ == '__main__':
    main()
