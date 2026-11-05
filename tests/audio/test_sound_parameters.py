from array import array
from dataclasses import replace
from types import SimpleNamespace
import wave

import numpy as np
import pytest
from PySide6.QtCore import QSettings

from ai_piano_studio.audio.sound_parameters import SoundParameters, SoundProcessor, map_velocities, process_export, velocity_response
from ai_piano_studio.audio.controller import PlaybackController, _AudioExportJob
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from tests.qt.test_playback import FakePlayer


def signal(rate=48000):
    times = np.arange(rate // 4) / rate
    return np.rint(np.column_stack((4000*np.sin(2*np.pi*6000*times), 3000*np.sin(2*np.pi*4000*times)))).astype('<i2')


def test_neutral_is_bit_exact_and_effects_are_block_invariant():
    source = signal()
    assert SoundProcessor(48000).process(source.tobytes()) == source.tobytes()
    parameters = SoundParameters(brightness=.8, reverb=.6, width=.8)
    whole = SoundProcessor(48000, parameters).process(source.tobytes())
    split = SoundProcessor(48000, parameters)
    pieces = b''.join(split.process(source[i:i+137].tobytes()) for i in range(0, len(source), 137))
    assert whole == pieces


def test_brightness_width_and_reverb_change_real_pcm():
    source = signal()
    soft = np.frombuffer(SoundProcessor(48000, SoundParameters(brightness=0)).process(source.tobytes()), dtype='<i2')
    bright = np.frombuffer(SoundProcessor(48000, SoundParameters(brightness=1)).process(source.tobytes()), dtype='<i2')
    assert np.std(bright) > np.std(soft) * 3
    mono = np.frombuffer(SoundProcessor(48000, SoundParameters(width=0)).process(source.tobytes()), dtype='<i2').reshape(-1, 2)
    assert np.array_equal(mono[:, 0], mono[:, 1])
    impulse = np.zeros((48000, 2), dtype='<i2')
    impulse[0] = 12000
    wet = np.frombuffer(SoundProcessor(48000, SoundParameters(reverb=1)).process(impulse.tobytes()), dtype='<i2').reshape(-1, 2)
    assert np.any(wet[1400:5000]) and not np.any(impulse[1400:])
    assert not np.array_equal(wet[:, 0], wet[:, 1])
    assert np.max(np.abs(wet)) < 32767


def test_reverb_stop_fades_and_disabling_does_not_resurrect_old_tail():
    processor = SoundProcessor(48000, SoundParameters(reverb=1))
    processor.process(signal().tobytes())
    processor.fade_out()
    tail = np.frombuffer(processor.process(bytes(4096*4)), dtype='<i2')
    assert not np.any(tail[576*2:])
    assert not np.any(np.frombuffer(processor.process(bytes(4096*4)), dtype='<i2'))
    processor.process(signal().tobytes())
    processor.set(SoundParameters(), immediate=True)
    processor.process(bytes(1024*4))
    processor.set(SoundParameters(reverb=1), immediate=True)
    assert not np.any(np.frombuffer(processor.process(bytes(4096*4)), dtype='<i2'))


def test_velocity_response_changes_note_on_without_modifying_score():
    note = Note.create(pitch=60, start_time=0, duration=1, velocity=64)
    assert velocity_response(64, 0) > 64 > velocity_response(64, 1)
    assert map_velocities((note,), 1)[0].velocity < 64
    assert note.velocity == 64
    from ai_piano_studio.domain.control_event import sustain_snapshot
    wrapped = sustain_snapshot((note,), ())
    assert map_velocities(wrapped, 0)[0].velocity > 64


def test_source_switch_restores_independent_parameters_and_settings(tmp_path, qapp):
    mixer = PianoVoiceMixer()
    player = FakePlayer()
    player.use_native_project_engine = lambda engine: None
    native = SimpleNamespace(drop_warm=lambda: None)
    controller = PlaybackController(EditorController(ProjectState()), player=player,
                                    piano_source=SimpleNamespace(mixer=mixer))
    controller._native_sound = native
    controller._sound_settings = QSettings(str(tmp_path/'sound.ini'), QSettings.IniFormat)
    controller.setProjectSoundParameter('brightness', .9)
    controller.setProjectSoundParameter('reverb', .6)
    controller._commit_sound('upright-sfz')
    assert controller.projectSoundBrightness == .5 and controller.projectSoundReverb == 0
    controller.setProjectSoundParameter('brightness', .1)
    controller.setProjectSoundParameter('response', .8)
    assert native.velocity_response_value == .8
    assert player.velocity_response_value == .8
    controller._commit_sound('salamander')
    assert controller.projectSoundBrightness == .9 and controller.projectSoundReverb == .6
    assert controller._sound_settings.value('sourceParameters/upright-sfz/brightness', type=float) == .1
    controller.resetProjectSoundParameters()
    assert controller.project_sound_parameters() == SoundParameters()
    assert controller.project_sound_parameters('upright-sfz').brightness == .1
    assert not controller._editor.state.active_notes


def test_tuning_is_restored_even_without_optional_native_pack(tmp_path, qapp):
    settings = QSettings(str(tmp_path/'sound.ini'), QSettings.IniFormat)
    settings.setValue('sourceTrim/salamander', 'invalid')
    settings.setValue('sourceParameters/salamander/reverb', .7)
    settings.setValue('sourceParameters/salamander/width', .3)
    mixer = PianoVoiceMixer()
    controller = PlaybackController(EditorController(ProjectState()), player=FakePlayer(),
                                    piano_source=SimpleNamespace(mixer=mixer))
    controller.configure_project_sound(tmp_path/'missing-pack', settings=settings)
    assert controller.soundParametersAvailable and not controller.projectSoundAvailable
    assert controller.projectSoundReverb == .7 and controller.projectSoundWidth == .3
    controller.setProjectSoundTrim(3)
    assert controller.project_sound_gain('salamander') > 1


def test_native_note_on_uses_updated_response_without_changing_note_off():
    from ai_piano_studio.audio.native_playback import NativeScoreStream
    from ai_piano_studio.audio.sfizz_renderer import _events_for_score
    events, frames = _events_for_score((Note.create(pitch=60, velocity=64, start_time=0, duration=.01),), sample_rate=48000)
    sent = []
    synth = SimpleNamespace(send=lambda event, offset: sent.append(event), block=lambda count: np.zeros(count*2, dtype='<i2'))
    stream = NativeScoreStream(synth, events, frames, lambda: 1)
    stream.pull(1024)
    assert next(event for event in sent if event.kind == 'on').value < 64
    assert next(event for event in sent if event.kind == 'off').data == 60


def test_export_uses_same_parameters_and_velocity_response(tmp_path, qapp):
    source = signal(44100)
    class Renderer:
        output_channels = 2
        def render_project(self, notes):
            self.velocity = notes[0].velocity
            return array('h', source.flatten())
    renderer = Renderer()
    parameters = SoundParameters(brightness=.8, response=.9, reverb=.5, width=.3)
    note = Note.create(pitch=60, start_time=0, duration=.25, velocity=70)
    path = tmp_path/'export.wav'
    job = _AudioExportJob(path, (note,), ACOUSTIC_PIANO_V2, renderer, 1, parameters)
    failures = []
    job.signals.failed.connect(failures.append)
    job.run()
    assert not failures
    expected, channels = process_export(array('h', source.flatten()), 44100, 2, parameters)
    with wave.open(str(path)) as wav:
        assert wav.getnchannels() == channels
        assert wav.readframes(wav.getnframes()) == expected.tobytes()
    assert renderer.velocity < 70 and note.velocity == 70


def test_settings_draft_preview_restore_apply_and_invalid_input(tmp_path, qapp, monkeypatch):
    mixer = PianoVoiceMixer()
    player = FakePlayer()
    controller = PlaybackController(EditorController(ProjectState()), player=player,
                                    piano_source=SimpleNamespace(mixer=mixer))
    settings = QSettings(str(tmp_path/'draft.ini'), QSettings.IniFormat)
    controller._sound_settings = settings
    monkeypatch.setattr(controller, 'auditionCurrentSound', lambda: None)
    values = dict(soundId='salamander', tone=.9, response=.8, reverb=.6, width=.2, sourceTrim=3)
    original = controller.project_sound_parameters()
    controller.previewSoundParameters(values)
    assert mixer._sound_effects.parameters.brightness == .9
    assert player.velocity_response_value == .8
    assert controller.project_sound_parameters() == original
    assert not settings.allKeys()
    controller.restoreSoundParameterPreview()
    assert mixer._sound_effects.parameters == original
    assert player.velocity_response_value == original.response
    controller.applySoundSettings('salamander', {**values, 'reverb': float('nan')})
    assert controller.project_sound_parameters() == original and not settings.allKeys()
    controller.applySoundSettings('salamander', values)
    assert controller.projectSoundTrim == 3
    assert controller.projectSoundReverb == .6
    assert settings.value('sourceParameters/salamander/brightness', type=float) == .9
    assert controller.project_sound_parameters('upright-sfz') == SoundParameters()
    assert not controller._editor.state.active_notes
