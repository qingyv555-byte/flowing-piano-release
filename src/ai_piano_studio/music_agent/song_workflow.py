"""Build and stage complete projects without mutating the active editor."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from uuid import uuid4

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.music_agent.song_resolver import DURATION_RE, ResolvedMusic, SourceCancelled
from ai_piano_studio.persistence.atomic import atomic_write_bytes
from ai_piano_studio.persistence.midi import materialize_midi_import, materialize_midi_controls
from ai_piano_studio.persistence.project_document import ProjectDocument, save_project_document, load_project_document
from ai_piano_studio.persistence.project_library import ProjectLibrary, PROJECT_FILENAME


@dataclass(frozen=True, slots=True)
class PreparedSong:
    staging_home: Path
    title: str
    state: ProjectState
    provenance: dict


def prepare_song(source: ResolvedMusic, instruction: str, home: Path, *, cancelled=lambda: False,
                 default_duration: float | None = None) -> PreparedSong:
    midi = source.midi
    changes = []
    if any(word in instruction for word in ("温柔", "柔和", "轻柔", "gentle", "soft")):
        midi = replace(midi, notes=tuple(replace(note, velocity=max(1, round(note.velocity * .75))) for note in midi.notes))
        changes.append("力度按原值的 75% 调整，最低 1")
    if any(word in instruction for word in ("慢一点", "更慢", "slower")):
        desired = midi.bpm * .85
        bpm = max(60, round(desired))
        if desired < 60:
            # Keep the existing editor BPM contract. Stretch note/control
            # positions to achieve the requested slowdown below that floor.
            ratio = bpm / desired
            midi = replace(midi, notes=tuple(replace(note,
                start_tick=round(note.start_tick * ratio),
                end_tick=max(round(note.start_tick * ratio) + 1, round(note.end_tick * ratio)))
                for note in midi.notes), controls=tuple(replace(control, tick=round(control.tick * ratio)) for control in midi.controls))
            changes.append("速度放慢 15%；低于 60 BPM 的部分通过延长音符及踏板时值实现")
        else:
            changes.append("速度按原值的 85% 调整")
        midi = replace(midi, bpm=bpm)
    requested = DURATION_RE.search(instruction)
    if (requested and float(requested.group(1)) > 0) or default_duration is not None:
        from ai_piano_studio.music_agent.source_confirmation import requested_excerpt_seconds
        seconds = requested_excerpt_seconds(instruction, default=default_duration or 15.0)
        limit = max(1, round(seconds * midi.bpm * midi.ppq / 60))
        midi = replace(midi, notes=tuple(replace(note, end_tick=min(note.end_tick, limit))
            for note in midi.notes if note.start_tick < limit),
            controls=tuple(control for control in midi.controls if control.tick < limit))
        changes.append(f"截取开头 {seconds:g} 秒")
    tempo = TempoMap(bpm=midi.bpm, beats_per_bar=midi.beats_per_bar, beat_unit=midi.beat_unit)
    notes, layers = materialize_midi_import(midi, tempo, status="saved")
    controls = materialize_midi_controls(midi, tempo)
    state = ProjectState(notes=notes, tempo=tempo, layers=layers, controls=controls)
    if not state.playback_notes or cancelled():
        raise SourceCancelled() if cancelled() else ValueError("no playable notes")
    provenance = dict(source.provenance, title=source.title, arrangementChanges=changes)
    return prepare_project(state, source.title, provenance, home, cancelled=cancelled)


def prepare_project(state: ProjectState, title: str, provenance: dict, home: Path, *, cancelled=lambda: False) -> PreparedSong:
    if not state.playback_notes:
        raise ValueError("no playable notes")
    pending = Path(home) / "assistant/pending-music"
    pending.mkdir(parents=True, exist_ok=True)
    staging = pending / uuid4().hex
    staging.mkdir()
    result = PreparedSong(staging, title, state, provenance)
    try:
        save_project_document(staging / PROJECT_FILENAME, ProjectDocument(state))
        atomic_write_bytes(staging / "music-source.json", json.dumps(provenance, ensure_ascii=False, indent=2).encode("utf-8"))
        notice = "\n".join(str(provenance.get(key, "")) for key in
                           ("title", "kind", "attribution", "license", "license_url", "source_url"))
        notice += "\nArrangement changes: " + "; ".join(provenance.get("arrangementChanges", [])) + "\n"
        notice += "This editable project may contain user or AI arrangement changes. Refer to the linked source edition for the original score.\n"
        atomic_write_bytes(staging / "MUSIC-SOURCE.txt", notice.encode("utf-8"))
        reopened = load_project_document(staging / PROJECT_FILENAME)
        if reopened.state.content != state.content:
            raise ValueError("generated project verification failed")
        if cancelled():
            raise SourceCancelled()
        return result
    except BaseException:
        discard_prepared_song(result, home)
        raise


def discard_prepared_song(song: PreparedSong, home: Path) -> None:
    expected = (Path(home) / "assistant/pending-music").resolve()
    staging = song.staging_home.resolve()
    if staging.parent != expected or len(staging.name) != 32:
        raise ValueError("staging path is outside task ownership")
    for name in (PROJECT_FILENAME, "music-source.json", "MUSIC-SOURCE.txt"):
        (staging / name).unlink(missing_ok=True)
    staging.rmdir()


def publish_prepared_song(song: PreparedSong, library: ProjectLibrary, home: Path) -> Path:
    """Publish only an already verified result, called after generation checks."""
    expected = (Path(home) / "assistant/pending-music").resolve()
    if song.staging_home.resolve().parent != expected:
        raise ValueError("staging path is outside task ownership")
    for name in (PROJECT_FILENAME, "music-source.json", "MUSIC-SOURCE.txt"):
        if not (song.staging_home / name).is_file():
            raise ValueError("incomplete staged project")
    title = library.unique_display_name(song.title)
    for _ in range(16):
        path = library.allocate_project_path()
        try:
            song.staging_home.rename(path.parent)
        except FileExistsError:
            continue
        break
    else:
        raise OSError("project library is busy")
    try:
        library.set_display_name(path, title)
        library.update_summary(path, song.state)
    except Exception:
        # Files are complete even when the optional library index is unwritable.
        import logging
        logging.getLogger(__name__).warning("Music project saved; library metadata update failed")
    return path
