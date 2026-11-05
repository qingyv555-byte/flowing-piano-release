from __future__ import annotations

import json
import logging
from pathlib import Path

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.control_event import ControlEvent
from ai_piano_studio.domain.music_layer import DEFAULT_LAYER_ID, MusicLayer
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import DEFAULT_BPM, DEFAULT_TIME_SIGNATURE, TempoMap


SCHEMA_VERSION = 5
_LOGGER = logging.getLogger(__name__)


def _project_state_to_dict(state: ProjectState) -> dict[str, object]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "bpm": state.tempo.bpm,
        "timeSignature": {
            "beatsPerBar": state.tempo.beats_per_bar,
            "beatUnit": state.tempo.beat_unit,
        },
        "notes": [note.to_dict() for note in state.notes],
        "musicLayers": [layer.to_dict() for layer in state.layers],
        "controlEvents": [event.to_dict() for event in state.controls],
        "playbackControlEvents": [event.to_dict() for event in state.playback_controls],
        "playbackNotes": [note.to_dict() for note in state.playback_notes],
        "playbackTempo": {
            "bpm": state.playback_tempo.bpm,
            "beatsPerBar": state.playback_tempo.beats_per_bar,
            "beatUnit": state.playback_tempo.beat_unit,
        },
    }


def _tempo_from_dict(data: dict) -> TempoMap:
    raw_bpm = data.get("bpm", DEFAULT_BPM)
    raw_signature = data.get("timeSignature") or {}
    if not isinstance(raw_signature, dict):
        raise ValueError("timeSignature must be an object")
    return TempoMap(
        bpm=raw_bpm,
        beats_per_bar=raw_signature.get("beatsPerBar", DEFAULT_TIME_SIGNATURE[0]),
        beat_unit=raw_signature.get("beatUnit", DEFAULT_TIME_SIGNATURE[1]),
    )


def _playback_tempo_from_dict(data: dict, fallback: TempoMap) -> TempoMap:
    raw = data.get("playbackTempo")
    if raw is None:
        return fallback
    if not isinstance(raw, dict):
        raise ValueError("playbackTempo must be an object")
    return TempoMap(
        bpm=raw.get("bpm", fallback.bpm),
        beats_per_bar=raw.get("beatsPerBar", fallback.beats_per_bar),
        beat_unit=raw.get("beatUnit", fallback.beat_unit),
    )


def _project_state_from_dict(data: object) -> ProjectState:
    if not isinstance(data, dict):
        raise ValueError("project file must contain a JSON object")
    schema_version = data.get("schemaVersion")
    if schema_version not in (1, 2, 3, 4, SCHEMA_VERSION):
        raise ValueError(f"unsupported schemaVersion: {data.get('schemaVersion')!r}")
    raw_notes = data.get("notes")
    if not isinstance(raw_notes, list):
        raise ValueError("project file must contain a notes list")
    if schema_version < 4:
        layers, note_layer_ids = _legacy_music_layers(raw_notes)
        notes = tuple(
            Note.from_dict({**item, "layerId": note_layer_ids[index]})
            for index, item in enumerate(raw_notes)
        )
    else:
        raw_layers = data.get("musicLayers")
        if not isinstance(raw_layers, list) or not raw_layers:
            raise ValueError("project file must contain a musicLayers list")
        layers = tuple(MusicLayer.from_dict(item) for item in raw_layers)
        notes = tuple(Note.from_dict(item) for item in raw_notes)
    tempo = _tempo_from_dict(data)
    raw_controls = data.get('controlEvents', [])
    raw_playback_controls = data.get('playbackControlEvents', raw_controls)
    if not isinstance(raw_controls, list) or not isinstance(raw_playback_controls, list):
        raise ValueError('control events must be lists')
    controls = tuple(ControlEvent.from_dict(e) for e in raw_controls)
    playback_controls = tuple(ControlEvent.from_dict(e) for e in raw_playback_controls)
    if schema_version < 3:
        return ProjectState(
            notes, tempo=tempo, playback_notes=notes,
            playback_tempo=tempo, layers=layers,
        )
    raw_playback_notes = data.get("playbackNotes")
    if not isinstance(raw_playback_notes, list):
        raise ValueError("project file must contain a playbackNotes list")
    note_membership = {note.id: note.layer_id for note in notes}
    playback_notes = tuple(
        Note.from_dict(
            {
                **item,
                "layerId": note_membership.get(
                    item.get("id"),
                    item.get("layerId", DEFAULT_LAYER_ID),
                ),
            }
        )
        for item in raw_playback_notes
    )
    note_ids = {note.id for note in notes}
    orphan_ids = sorted({note.id for note in playback_notes} - note_ids)
    if orphan_ids:
        _LOGGER.warning(
            "Ignoring playback notes that are missing from the authoritative notes list: %s",
            ", ".join(orphan_ids),
        )
        playback_notes = tuple(note for note in playback_notes if note.id in note_ids)
    playback_tempo = _playback_tempo_from_dict(data, tempo)
    if not any(note.status != "saved" for note in notes):
        if playback_notes != notes or playback_tempo != tempo:
            _LOGGER.warning(
                "Ignoring stale playback snapshot because the saved notes are authoritative"
            )
        playback_notes = notes
        playback_tempo = tempo
    return ProjectState(
        notes,
        tempo=tempo,
        playback_notes=playback_notes,
        playback_tempo=playback_tempo,
        layers=layers,
        controls=controls,
        playback_controls=playback_controls,
    )


def _legacy_music_layers(
    raw_notes: list[object],
) -> tuple[tuple[MusicLayer, ...], tuple[str, ...]]:
    """Map pre-v4 MIDI routing to deterministic first-class layers."""
    routes: list[tuple[int | None, int | None]] = []
    for item in raw_notes:
        if not isinstance(item, dict):
            raise ValueError("each note must be an object")
        route = (item.get("track"), item.get("channel"))
        if route not in routes:
            routes.append(route)
    if not routes:
        routes.append((None, None))

    route_ids: dict[tuple[int | None, int | None], str] = {}
    layers: list[MusicLayer] = []
    single_unrouted = routes == [(None, None)]
    for index, (track, channel) in enumerate(routes):
        if single_unrouted or (track is None and channel is None):
            layer_id = DEFAULT_LAYER_ID
            name = "默认音层"
        else:
            track_token = "none" if track is None else str(track)
            channel_token = "none" if channel is None else str(channel)
            layer_id = f"layer-midi-{track_token}-{channel_token}"
            details = []
            if track is not None:
                details.append(f"轨道 {track + 1}")
            if channel is not None:
                details.append(f"通道 {channel + 1}")
            name = " · ".join(details) or "默认音层"
        route_ids[(track, channel)] = layer_id
        layers.append(MusicLayer.create(id=layer_id, name=name, order=index))

    return (
        tuple(layers),
        tuple(
            route_ids[(item.get("track"), item.get("channel"))]
            for item in raw_notes
            if isinstance(item, dict)
        ),
    )


def project_to_dict(state: ProjectState) -> dict[str, object]:
    from ai_piano_studio.harmony.project_metadata import HarmonyProjectMetadata
    from ai_piano_studio.persistence.project_document import ProjectDocument, project_document_to_dict

    return project_document_to_dict(ProjectDocument(state, HarmonyProjectMetadata.empty()))


def project_from_dict(data: object) -> ProjectState:
    from ai_piano_studio.persistence.project_document import project_document_from_dict

    return project_document_from_dict(data).state


def load_project(path: str | Path) -> ProjectState:
    from ai_piano_studio.persistence.project_document import load_project_document

    return load_project_document(path).state


def project_to_json(state: ProjectState) -> str:
    return json.dumps(project_to_dict(state), ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def save_project(path: str | Path, state: ProjectState) -> None:
    from ai_piano_studio.harmony.project_metadata import HarmonyProjectMetadata
    from ai_piano_studio.persistence.project_document import ProjectDocument, save_project_document

    # A project save commits pending edits and repairs a stale playback cache.
    # Keep an already-saved notes tuple intact so saving does not rewrite note
    # timestamps merely to rebuild the derived snapshot.
    if state.has_unsynced_changes:
        persisted_state = state.synchronize()
    elif state.playback_notes != state.notes or state.playback_tempo != state.tempo:
        persisted_state = ProjectState(
            state.notes,
            state.selected_ids,
            state.tempo,
            state.notes,
            state.tempo,
            state.layers,
            state.controls,
            state.controls,
        )
    else:
        persisted_state = state
    save_project_document(path, ProjectDocument(persisted_state, HarmonyProjectMetadata.empty()))
