from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot

from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.persistence.midi import MidiImportResult, load_midi


logger = logging.getLogger(__name__)

_WARNING_TEXT = {
    'dangling_sustain': '文件末尾缺少抬踏板；保留原始事件，播放与导出在作品末尾安全释放。',
    'multi_time_signature': '文件含多个拍号，当前使用首个受支持拍号。',
    'unsupported_pedals': 'CC66 / CC67 暂不导入或模拟；CC64 保留原值，按 64 阈值切换。',
    'unsupported_channel_modes': '通道模式命令暂不模拟；不会将 All Notes Off 当成 All Sound Off。',
    "dangling_note_on": "有未结束音符，已按轨道结尾补齐。",
    "multi_tempo": "文件包含多个速度，已使用首个速度。",
    "tempo_clamped": "文件速度超出范围，已限制到可用 BPM。",
    "unmatched_note_off": "发现没有对应 Note On 的结束事件，已忽略。",
    "unsupported_time_signature": "文件拍号不受支持，已使用 4/4。",
    "zero_length_note": "发现零时长音符，已忽略。",
}


class MidiImportController(QObject):
    """QML bridge for parsing one local MIDI file and confirming its import."""

    imported = Signal(str)
    feedbackRequested = Signal(str)
    changed = Signal()

    def __init__(self, editor: EditorController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._editor = editor
        self._result: MidiImportResult | None = None
        self._source_path: Path | None = None
        self._project_session_id: int | None = None
        self._editor.stateChanged.connect(self._on_editor_state_changed)

    @Property(str, notify=changed)
    def sourceName(self) -> str:
        return self._source_path.name if self._source_path else ""

    @Property(bool, notify=changed)
    def ready(self) -> bool:
        return self._result is not None

    @Property(int, notify=changed)
    def noteCount(self) -> int:
        return len(self._result.notes) if self._result else 0

    @Property(int, notify=changed)
    def controlCount(self) -> int:
        return len(self._result.controls) if self._result else 0

    @Property(int, notify=changed)
    def trackCount(self) -> int:
        return self._result.track_count if self._result else 0

    @Property(int, notify=changed)
    def noteTrackCount(self) -> int:
        return self._result.note_track_count if self._result else 0

    @Property(int, notify=changed)
    def bpm(self) -> int:
        return self._result.bpm if self._result else 120

    @Property(int, notify=changed)
    def beatsPerBar(self) -> int:
        return self._result.beats_per_bar if self._result else 4

    @Property(int, notify=changed)
    def beatUnit(self) -> int:
        return self._result.beat_unit if self._result else 4

    @Property("QStringList", notify=changed)
    def warnings(self) -> list[str]:
        if not self._result:
            return []
        return [_WARNING_TEXT.get(code, code) for code in self._result.warnings]

    @Slot(str, result=bool)
    def prepare(self, source: str) -> bool:
        self.cancel()
        try:
            local_path = Path(self._editor._local_path(source))
            if local_path.suffix.lower() not in {".mid", ".midi"}:
                raise ValueError("请选择 .mid 或 .midi 文件")
            result = load_midi(local_path)
        except (OSError, ValueError) as exc:
            logger.info("MIDI import rejected: %s", exc)
            self.feedbackRequested.emit(f"无法读取 MIDI：{exc}")
            return False
        self._source_path = local_path
        self._result = result
        self._project_session_id = self._editor.projectSessionId
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def importResult(self, mode: str = "append") -> bool:
        if self._project_session_id != self._editor.projectSessionId:
            self.feedbackRequested.emit("工程已切换，MIDI 导入结果已失效")
            return False
        if self._result is None:
            self.feedbackRequested.emit("请先选择 MIDI 文件")
            return False
        if mode not in {"append", "replace"}:
            self.feedbackRequested.emit("MIDI 导入方式无效")
            return False
        if not self._editor.importMidiResult(self._result, mode):
            return False
        summary = (
            f"已导入 {self.noteCount} 个 MIDI 音符 · "
            f"BPM {self.bpm} · {self.beatsPerBar}/{self.beatUnit}"
        )
        self.imported.emit(summary)
        self.cancel()
        return True

    @Slot()
    def cancel(self) -> None:
        had_value = self._result is not None or self._source_path is not None
        self._result = None
        self._source_path = None
        self._project_session_id = None
        if had_value:
            self.changed.emit()

    @Slot()
    def _on_editor_state_changed(self) -> None:
        if self._project_session_id is None:
            return
        if self._project_session_id == self._editor.projectSessionId:
            return
        self.cancel()
