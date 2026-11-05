from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.controller import HarmonySuggestionController
from ai_piano_studio.harmony.settings import HarmonySettings


class FakeAuditionPlayer(QObject):
    playingChanged = Signal()
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.calls = []
        self.stop_calls = 0

    def audition(self, pitches, velocity: int, volume: float) -> None:
        self.calls.append((list(pitches), velocity, volume))

    def stop(self) -> None:
        self.stop_calls += 1


class FakePlayback(QObject):
    playingChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.playing = False
        self.volume = 0.42

    def set_playing(self, value: bool) -> None:
        self.playing = value
        self.playingChanged.emit()


class FakeKeyboard(QObject):
    recordingChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.recording = False

    def set_recording(self, value: bool) -> None:
        self.recording = value
        self.recordingChanged.emit()


class PublicPreviewModel(QObject):
    def set_project_identity_provider(self, _provider) -> None:
        pass

    def candidate_pitches(self, measure_index: int, candidate_index: int):
        if (measure_index, candidate_index) == (2, 0):
            return (60, 64, 67)
        return None

    def clear(self) -> None:
        pass


def make_controller():
    editor = EditorController(ProjectState())
    settings = HarmonySettings()
    playback = FakePlayback()
    keyboard = FakeKeyboard()
    audition = FakeAuditionPlayer()
    controller = HarmonySuggestionController(
        editor,
        settings,
        playback=playback,
        keyboard=keyboard,
        audition_player=audition,
    )
    controller.previewModel._rows = [
        {"measureIndex": 2, "candidatePitches": [(60, 64, 67)]}
    ]
    return controller, editor, settings, playback, keyboard, audition


def test_audition_candidate_uses_preview_pitches_and_reads_playback_volume(qapp) -> None:
    controller, _editor, _settings, _playback, _keyboard, audition = make_controller()

    controller.auditionCandidate(2, 0)

    assert audition.calls == [([60, 64, 67], 75, 0.42)]


def test_audition_candidate_uses_the_preview_public_lookup(qapp) -> None:
    editor = EditorController(ProjectState())
    playback = FakePlayback()
    audition = FakeAuditionPlayer()
    controller = HarmonySuggestionController(
        editor,
        HarmonySettings(),
        preview_model=PublicPreviewModel(),
        playback=playback,
        keyboard=FakeKeyboard(),
        audition_player=audition,
    )

    controller.auditionCandidate(2, 0)

    assert audition.calls == [([60, 64, 67], 75, 0.42)]


def test_playback_or_recording_rejects_audition_without_changing_playback_state(qapp) -> None:
    controller, _editor, _settings, playback, keyboard, audition = make_controller()
    feedback = []
    controller.feedbackRequested.connect(feedback.append)
    playback.set_playing(True)

    controller.auditionCandidate(2, 0)

    assert controller.canAudition is False
    assert audition.calls == []
    assert playback.playing is True
    assert feedback == ["请先暂停播放，再试听和弦"]

    playback.set_playing(False)
    keyboard.set_recording(True)
    controller.auditionCandidate(2, 0)

    assert controller.canAudition is False
    assert audition.calls == []
    assert keyboard.recording is True


def test_audition_failure_reports_feedback_without_changing_playback_state(qapp) -> None:
    controller, _editor, _settings, playback, _keyboard, audition = make_controller()
    feedback = []
    controller.feedbackRequested.connect(feedback.append)

    audition.failed.emit("无法试听和弦")

    assert feedback == ["无法试听和弦"]
    assert playback.playing is False


def test_lifecycle_events_stop_the_isolated_audition(qapp) -> None:
    controller, editor, settings, playback, keyboard, audition = make_controller()

    settings.setMode("preview")
    playback.set_playing(True)
    keyboard.set_recording(True)
    controller.windowDeactivated()
    controller.leaveEditor()
    settings.setMode("off")
    editor.currentFileChanged.emit()
    controller.close()

    assert audition.stop_calls == 7
