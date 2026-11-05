"""Scoped, offline audition analysis; no hardware playback or project mutation."""
from __future__ import annotations

from dataclasses import replace
import math

import numpy as np

from ai_piano_studio.audio.synth import SAMPLE_RATE, render_project
from ai_piano_studio.domain.control_event import sustain_snapshot, sounding_duration
from ai_piano_studio.music_agent.song_resolver import SourceCancelled
from ai_piano_studio.music_agent.tool_contracts import MusicPermission, MusicToolResult
from ai_piano_studio.music_agent.tool_registry import MusicToolDefinition, MusicToolRegistry


ANALYZE_AUDITION = MusicToolDefinition(
    'analyze_audition',
    'Render a scoped excerpt of the current or staged music offline and inspect audible signal statistics. '
    'Reports actual rendered peak/RMS/clipping, note timing/density and register. It does not start speakers, '
    'save the project, or establish a match to a recording. Read-only; works after staging notes. '
    'Choose up to 30 seconds of authorized bars per call, then move to later bars as needed.',
    dict(type='object', additionalProperties=False, required=['start_bar', 'end_bar'], properties={
        'start_bar': dict(type='integer', minimum=1), 'end_bar': dict(type='integer', minimum=1)}),
    MusicPermission.READ_NOTES, True)


def with_audition_analysis(registry):
    return MusicToolRegistry([*(registry.lookup(n) for n in registry.names), ANALYZE_AUDITION])


def audition_snapshot(gateway, arguments, context, playback=None):
    """Runs on the Qt thread; captures immutable notes and a renderer snapshot."""
    from ai_piano_studio.music_agent.tool_gateway import (
        MusicToolValidationError, MusicToolPermissionError, MusicToolConflictError, MusicToolLimitError)
    try:
        gateway._require_current_context(context)
        gateway._require_permission(ANALYZE_AUDITION, context)
        payload = gateway._validate_schema(ANALYZE_AUDITION.input_schema, arguments)
        requested = gateway._requested_bar_range(payload)
        gateway._require_authorized_range(requested, context,
                                         whole_project_permission=MusicPermission.WHOLE_PROJECT_READ)
        state = gateway._effective_state(context)
        # A proposal carries the original playback snapshot until adoption.
        # Materialize staged active notes only in this private analysis copy.
        state = state.synchronize()
        bar_seconds = state.tempo.beats_per_bar * 4 / state.tempo.beat_unit * 60 / state.tempo.bpm
        start, end = (requested.start_bar - 1) * bar_seconds, requested.end_bar * bar_seconds
        if end - start > 30:
            raise MusicToolValidationError('Choose fewer bars: each audition analysis covers at most 30 seconds.')
        context.budget.consume_step()
        wrapped = sustain_snapshot(state.effective_notes(for_playback=True), state.effective_controls(True))
        notes = tuple(replace(getattr(item, 'note', item), start_time=max(start, item.start_time) - start,
                              duration=min(end, item.start_time + sounding_duration(item)) - max(start, item.start_time))
                      for item in wrapped if item.start_time < end and item.start_time + sounding_duration(item) > start)
        if len(notes) > 2000:
            raise MusicToolValidationError('Choose a shorter interval: this excerpt has more than 2000 notes.')
        renderer, gain, source = None, 1.0, 'built_in_piano_analysis'
        if playback is not None:
            sound_id = getattr(playback, '_sound_id', '')
            if sound_id == 'upright-sfz' and getattr(playback, '_native_sound', None) is not None:
                renderer = playback._native_sound.renderer
            elif getattr(playback, 'piano_source', None) is not None:
                renderer = playback.piano_source.snapshot_renderer()
            if renderer is not None:
                source = sound_id or 'current_piano_source'
                gain = playback.project_sound_gain(sound_id, export=True) if sound_id and hasattr(playback, 'project_sound_gain') else 1.0
        return dict(notes=notes, renderer=renderer, gain=gain, source=source,
                    start_seconds=start, end_seconds=end, start_bar=requested.start_bar, end_bar=requested.end_bar)
    except (MusicToolValidationError, MusicToolPermissionError, MusicToolConflictError, MusicToolLimitError) as error:
        code = {MusicToolValidationError: 'validation_error', MusicToolPermissionError: 'permission_denied',
                MusicToolConflictError: 'conflict', MusicToolLimitError: 'limit_exceeded'}[type(error)]
        return MusicToolResult.failure('analyze_audition', code, str(error))


def analyze_audition(snapshot, cancelled=lambda: False):
    """Runs on the provider worker, using the same offline renderer as export."""
    if isinstance(snapshot, MusicToolResult):
        return snapshot
    if cancelled():
        raise SourceCancelled()
    notes, renderer = snapshot['notes'], snapshot['renderer']
    try:
        if renderer is None:
            samples = render_project(notes, should_cancel=cancelled)
            rate, channels = SAMPLE_RATE, 1
        else:
            samples = renderer.render_project_cancellable(notes, cancelled)
            rate = getattr(renderer, 'export_sample_rate', SAMPLE_RATE)
            channels = renderer.output_channels
        if cancelled():
            raise SourceCancelled()
        if type(rate) is not int or rate <= 0 or channels not in (1, 2):
            raise ValueError('Unsupported audio renderer format')
        from ai_piano_studio.audio.sound_gain import scale_export
        pcm = np.asarray(scale_export(samples, snapshot['gain']), dtype=np.int16)
        values = pcm.astype(np.float64) / 32768
        peak = float(np.max(np.abs(values))) if len(values) else 0.0
        rms = float(np.sqrt(np.mean(values * values))) if len(values) else 0.0
        events = sorted([(n.start_time, 1) for n in notes] + [(n.start_time + n.duration, -1) for n in notes])
        active = polyphony = 0
        for _, delta in events:
            active += delta
            polyphony = max(polyphony, active)
        span = snapshot['end_seconds'] - snapshot['start_seconds']
        data = {key: snapshot[key] for key in ('start_bar', 'end_bar', 'start_seconds', 'end_seconds', 'source')}
        data.update(status='analyzed', analysis_kind='rendered_pcm_statistics', note_count=len(notes),
                    rendered_seconds=len(pcm) / (rate * channels), sample_rate=rate, channels=channels,
                    peak_dbfs=round(20 * math.log10(peak), 2) if peak else None,
                    rms_dbfs=round(20 * math.log10(rms), 2) if rms else None,
                    clipped_sample_count=int(np.count_nonzero(np.abs(values) >= 32767 / 32768)),
                    audible=peak > 1 / 32768, max_polyphony=polyphony,
                    notes_per_second=round(len(notes) / span, 3),
                    min_pitch=min((n.pitch for n in notes), default=None),
                    max_pitch=max((n.pitch for n in notes), default=None),
                    warning='Signal and note analysis, not semantic audio listening or original-recording verification.')
        return MusicToolResult.success_result('analyze_audition', 'Audition rendered and analyzed offline.', data=data)
    except (OSError, RuntimeError, ValueError) as error:
        if isinstance(error, SourceCancelled):
            raise
        return MusicToolResult.failure('analyze_audition', 'resource_unavailable',
            'This excerpt could not be rendered for analysis.', data=dict(status='unavailable', reason=type(error).__name__,
                next_action='Try a shorter excerpt or inspect the notes; do not claim the audio was analyzed.'))
