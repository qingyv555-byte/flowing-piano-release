"""Opt-in REAL Codex acceptance, isolated project/profile; never touches live work."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QUICK_BACKEND", "software")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.assistant_controller import MusicAssistantController
from ai_piano_studio.music_agent.codex_provider import CodexProvider, detect_codex
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.project_document import load_project_document
from ai_piano_studio.persistence.midi import export_midi
from tools.studio_evidence import install_memory_output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default="gpt-6.1-sol")
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists():
        raise SystemExit("Use a fresh output directory; existing evidence is never overwritten.")
    out.mkdir(parents=True)
    home = out / "profile"
    home.mkdir()
    app = QApplication([])
    app.setOrganizationName("AIPianoStudio")
    app.setApplicationName("AI Piano Studio Rebuild")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope, str(home / "settings"))
    assert Path(QSettings().fileName()).resolve().is_relative_to(home)
    blocked_audio = install_memory_output()

    def write_json(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    class RecordingCodex(CodexProvider):
        """Records public transport input/output; always calls the real provider."""
        def stream_events(self, messages, tools, reasoning, attachments, **kwargs):
            with (out / "model-requests.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"messages": list(messages), "tools": list(tools)}, ensure_ascii=False) + "\n")
            for event in super().stream_events(messages, tools, reasoning, attachments, **kwargs):
                with (out / "model-responses.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(asdict(event), ensure_ascii=False, default=str) + "\n")
                yield event

    info = detect_codex()
    if not info.get("supported") or info.get("login") != "chatgpt":
        write_json(out / "preflight.json", info)
        raise SystemExit("Real Codex connection unavailable; see preflight.json")
    models = CodexProvider(args.model).list_models()
    if args.model not in models:
        raise SystemExit(f"Requested model is unavailable: {args.model}")
    settings = ProviderSettingsService(settings=QSettings(), credentials=MemoryCredentialStore(),
        provider_factory=lambda _id: RecordingCodex(args.model, timeout_seconds=600))
    settings.update_codex_status({**info, "checked": True})
    settings.set_dynamic_models("codex", models)

    layers = (MusicLayer.create(id="live-melody", name="主旋律", role="melody"),
              MusicLayer.create(id="live-bass", name="伴奏", role="bass", order=1))
    pitches = [60, 62, 64, 67, 65, 64, 62, 59, 60, 64, 67, 69, 67, 65, 62, 60, 62, 64, 59, 60]
    notes = [Note.create(id=f"opening-m-{i}", pitch=p, start_time=i * .5, duration=.5,
                        velocity=75, layer_id=layers[0].id) for i, p in enumerate(pitches)]
    for i, chord in enumerate(((48, 52, 55), (45, 48, 52), (53, 57, 60), (43, 47, 50), (48, 52, 55))):
        notes.extend(Note.create(id=f"opening-b-{i}-{p}", pitch=p, start_time=i * 2,
                     duration=2, velocity=48, layer_id=layers[1].id) for p in chord)
    baseline = ProjectState(notes=tuple(notes), tempo=TempoMap(bpm=60), layers=layers)
    library = ProjectLibrary(home / "Projects")
    editor = EditorController(baseline, project_library=library)
    editor.setAutosavePolicy(False, 60)
    assert editor.saveNamedProject("真实模型记忆验收－续写25秒")

    def pump_until(predicate, timeout=1200):
        deadline, last = time.monotonic() + timeout, 0
        while not predicate():
            app.processEvents()
            time.sleep(.01)
            if time.monotonic() >= deadline:
                raise TimeoutError("Real-model acceptance timed out")
            if "assistant" in locals_scope and time.monotonic() - last > 15:
                a = locals_scope["assistant"]
                print(json.dumps({"busy": a.isBusy, "stage": a.taskProgress.get("stage"),
                    "elapsed": a.taskProgress.get("elapsedSeconds"), "notes": len(editor.state.active_notes)}, ensure_ascii=False), flush=True)
                last = time.monotonic()
        app.processEvents()

    locals_scope = {}
    pump_until(lambda: not editor.isSaving)
    path = Path(editor.currentFilePath)
    export_midi(out / "opening-10s.mid", baseline.active_notes, baseline.tempo, layers=baseline.layers)
    assistant = MusicAssistantController(editor, provider_settings=settings,
        history_path=home / "assistant" / "conversation.sqlite3", automatic_music=True)
    locals_scope["assistant"] = assistant
    assistant._on_codex_detection_finished(info)
    settings.set_dynamic_models("codex", models)
    assert assistant.selectProvider("codex")
    assert assistant.selectModel(args.model)
    assert assistant.currentModelStatus["canCreate"], assistant.currentModelStatus
    assistant.setCodexWaitSeconds(600)
    turns = []
    evidence = {"model": args.model, "startedAt": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                "projectPath": str(path), "profileHome": str(home), "seed": "New synthetic original exercise; no user project read or copied", "turns": turns}

    def snapshot(label):
        editor.synchronizeNow()
        turns.append({"label": label, "result": assistant.lastTurnResult,
            "progress": assistant.taskProgress, "duration": editor.state.playback_duration,
            "notes": len(editor.state.active_notes), "task": deepcopy(assistant._task_memory.state)})
        write_json(out / "evidence.json", evidence)
        write_json(out / "chat.json", [m.to_dict() for m in assistant.messages.entries])
        (out / "chat.md").write_text("# 真实模型测试聊天\n\n" + "\n\n".join(
            f"**{m.kind.value}**\n\n{m.text}" for m in assistant.messages.entries), encoding="utf-8")

    try:
        assert assistant.sendMessage("请将当前10秒练习接着续写到25秒，不要重复或循环开头。保留已有音符和两个声部，主旋律向后发展，新增内容从10秒之后开始。请实际完成并直接修改进去。")
        began = time.monotonic()
        pump_until(lambda: not assistant.isBusy or assistant.taskProgress.get("stage") in {"submitted", "responding"}
                   or time.monotonic() - began > 8, timeout=30)
        evidence["hiInsertedWhileBusy"] = assistant.isBusy
        assert assistant.sendMessage("hi")
        pump_until(lambda: not assistant.isBusy)
        snapshot("extension_with_hi")
        assert assistant.sendMessage("刚才让你续写到25秒、不要重复开头，现在进度怎么样？请读取当前工程确认实际时长和音符，不要重新创作，也不要因为我说了hi就忘掉原任务。")
        pump_until(lambda: not assistant.isBusy)
        snapshot("progress_query")
    finally:
        editor.synchronizeNow()
        editor.saveCurrent()
        pump_until(lambda: not editor.isSaving, timeout=30)
        snapshot("saved")
        saved = load_project_document(path).state
        def musical_fields(note):
            return (note.id, note.pitch, note.start_time, note.duration, note.velocity,
                    note.layer_id, note.instrument, note.release_mode, note.release_ms)
        saved_by_id = {n.id: n for n in saved.active_notes}
        opening_unchanged = all(old.id in saved_by_id and musical_fields(saved_by_id[old.id]) == musical_fields(old)
                                for old in baseline.active_notes)
        appended = [n for n in saved.active_notes if n.id not in {x.id for x in baseline.active_notes}]
        melody = sorted((n for n in appended if n.layer_id == layers[0].id), key=lambda n: n.start_time)
        opening_signature = [(n.pitch, round(n.start_time, 4), round(n.duration, 4)) for n in baseline.active_notes if n.layer_id == layers[0].id]
        repeated_opening = []
        for start in melody:
            shifted = {(n.pitch, round(n.start_time - start.start_time, 4), round(n.duration, 4)) for n in melody
                       if start.start_time <= n.start_time < start.start_time + 10}
            if all(signature in shifted for signature in opening_signature):
                repeated_opening.append(start.start_time)
        evidence["checks"] = {"durationSeconds": saved.playback_duration,
            "reached25Seconds": saved.playback_duration >= 24.95,
            "openingUnchanged": opening_unchanged, "addedNotes": len(appended),
            "newNotesStartAtOrAfter10": all(n.start_time >= 10 for n in appended),
            "exactOpeningCopiesInAddedMelody": repeated_opening,
            "realModelRequests": sum(1 for _ in (out / "model-requests.jsonl").open(encoding="utf-8")),
            "physicalAudioAttempts": blocked_audio}
        export_midi(out / "result.mid", saved.active_notes, saved.tempo, layers=saved.layers)
        write_json(out / "evidence.json", evidence)
        (out / "Open-Test.cmd").write_text('@echo off\r\nchcp 65001 >nul\r\ncall "' + str(ROOT / "Open-Studio.cmd") + '" --profile-home "' + str(home) + '"\r\n', encoding="utf-8")
        print(json.dumps(evidence["checks"], ensure_ascii=False), flush=True)
        print("EVIDENCE=" + str(out), flush=True)
        assistant.shutdown()
    return 0 if evidence["checks"]["reached25Seconds"] and opening_unchanged and not repeated_opening else 1


if __name__ == "__main__":
    raise SystemExit(main())
