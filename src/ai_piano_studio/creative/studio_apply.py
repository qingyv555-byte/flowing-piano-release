"""Explicit append boundary; the creative workspace never owns the live editor."""
from dataclasses import replace
from uuid import uuid4

from ai_piano_studio.domain.music_layer import MusicLayer


def append_theme(editor, theme, studio_busy):
    if studio_busy():
        raise ValueError('请先停止工作室播放或录制，再追加主题')
    notes = theme.effective_notes(for_playback=True)
    if not 1 <= len(notes) <= 256 or max(n.start_time + n.duration for n in notes) > 120:
        raise ValueError('请选择不超过 256 个音符、120 秒的主题')
    before = editor.state
    offset = before.estimated_duration
    layer = MusicLayer.create(name='灵感主题', order=len(before.layers))
    added = tuple(replace(n, id=str(uuid4()), layer_id=layer.id,
                          start_time=n.start_time + offset, status='saved') for n in notes)
    after = replace(before, notes=before.notes + added,
                    playback_notes=before.playback_notes + added,
                    layers=before.layers + (layer,))
    editor.begin_agent_turn()
    try:
        editor.preview_agent_state(after, editor.harmony_metadata)
        editor.commit_agent_turn()
    except Exception:
        if editor._agent_turn_active:
            editor.cancel_agent_turn()
        raise
