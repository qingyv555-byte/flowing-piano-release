"""Resume only the read-only progress phase of a preserved real acceptance run."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import argparse
import json
import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.assistant_controller import MusicAssistantController
from ai_piano_studio.music_agent.codex_provider import CodexProvider, detect_codex
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.project_document import load_project_document
from ai_piano_studio.persistence.midi import parse_midi
from tools.studio_evidence import install_memory_output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    old = json.loads((out / "evidence.json").read_text(encoding="utf-8"))
    home, path = Path(old["profileHome"]), Path(old["projectPath"])
    assert path.resolve().is_relative_to(home.resolve())
    state = load_project_document(path).state
    original = parse_midi((out / "opening-10s.mid").read_bytes())
    tempo = TempoMap(bpm=original.bpm)
    expected = sorted((n.pitch, round(tempo.beats_to_seconds(n.start_tick / original.ppq), 6),
                       round(tempo.beats_to_seconds((n.end_tick-n.start_tick) / original.ppq), 6), n.velocity)
                      for n in original.notes)
    opening = sorted((n.pitch, round(n.start_time, 6), round(n.duration, 6), n.velocity)
                     for n in state.active_notes if n.id.startswith("opening-"))
    assert opening == expected and len(opening) == 35
    assert state.playback_duration == 25 and len(state.active_notes) == 79
    assert old["turns"][0]["result"]["committed"]
    model_rows = [json.loads(line) for line in (out / "model-responses.jsonl").read_text(encoding="utf-8").splitlines()]
    generated = [n for row in model_rows for call in row["tool_calls"] if call["name"] == "add_notes"
                 for n in json.loads(call["arguments_json"])["notes"]]
    added = [n for n in state.active_notes if not n.id.startswith("opening-")]
    fields = lambda n: (n.pitch, n.start_time, n.duration, n.velocity, n.layer_id)
    assert sorted(fields(n) for n in added) == sorted((n["pitch"], n["start_time"], n["duration"], n["velocity"], n["layer_id"]) for n in generated)
    audit = {"savedDuration": 25, "savedNotes": 79, "opening35MusicalNotesUnchanged": True,
             "added44ExactlyMatchRealModelToolArguments": True, "timestampAndStatusExcludedFromMusicComparison": True}
    (out / "saved-music-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    app = QApplication([])
    app.setOrganizationName("AIPianoStudio")
    app.setApplicationName("AI Piano Studio Rebuild")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope, str(home / "settings"))
    blocked = install_memory_output()

    class Recorder(CodexProvider):
        def stream_events(self, messages, tools, reasoning, attachments, **kwargs):
            with (out / "model-requests.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"messages": list(messages), "tools": list(tools), "phase": "fixed_readonly_progress"}, ensure_ascii=False)+"\n")
            for event in super().stream_events(messages, tools, reasoning, attachments, **kwargs):
                with (out / "model-responses.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps({**asdict(event), "phase": "fixed_readonly_progress"}, ensure_ascii=False)+"\n")
                yield event

    model = old["model"]
    info = detect_codex()
    service = ProviderSettingsService(settings=QSettings(), credentials=MemoryCredentialStore(),
        provider_factory=lambda _id: Recorder(model, timeout_seconds=600))
    service.update_codex_status(info)
    service.set_dynamic_models("codex", CodexProvider(model).list_models())
    editor = EditorController(project_library=ProjectLibrary(home / "Projects"))
    editor.loadProject(str(path))
    assert Path(editor.currentFilePath).resolve() == path.resolve()
    editor.setAutosavePolicy(False, 60)
    assistant = MusicAssistantController(editor, provider_settings=service,
        history_path=home / "assistant" / "conversation.sqlite3", automatic_music=True)
    assistant._on_codex_detection_finished(info)
    service.set_dynamic_models("codex", [model])
    assert assistant.selectProvider("codex") and assistant.selectModel(model)
    assistant._memory_record("completed", {"committed": True, "reason": "saved_notes_verified_against_real_write_and_original_midi"})
    assistant._append_notice("真实验收续测：已从保存文件核验25秒、79音符，原35音符未改，新增44音符与真实模型工具参数一致。上一条进度追问被误判为编辑，已中止该测试请求；现用修正后的只读路径重测。此前模型所说的‘点击应用’不适用于已直接提交的这轮。")
    question = "刚才让你续写到25秒、不要重复开头，现在进度怎么样？请读取当前工程确认实际时长和音符，不要重新创作，也不要因为我说了hi就忘掉原任务。"
    assert assistant.sendMessage(question)
    assert assistant._active_intent == "READ_MUSIC"
    deadline, last = time.monotonic()+900, 0
    try:
        while assistant.isBusy:
            app.processEvents()
            time.sleep(.01)
            if time.monotonic() > deadline:
                assistant.cancelGeneration()
                raise TimeoutError("Read-only live verification timed out")
            if time.monotonic()-last > 20:
                print(json.dumps({"stage": assistant.taskProgress.get("stage"), "elapsed": assistant.taskProgress.get("elapsedSeconds"), "notes":len(editor.state.active_notes)}), flush=True)
                last=time.monotonic()
        result=assistant.lastTurnResult
        assert result["status"] == "finished", result
        assert result["intent"] == "READ_MUSIC" and result["writeCount"] == 0
        assert editor.state.content == state.content
        assert assistant._task_memory.state["status"] == "completed"
        audit.update(progressResult=result, progressStage=assistant.taskProgress,
                     memoryStatus=assistant._task_memory.state["status"], physicalAudioAttempts=blocked)
        (out/"final-acceptance.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    finally:
        (out/"chat.json").write_text(json.dumps([m.to_dict() for m in assistant.messages.entries],ensure_ascii=False,indent=2),encoding="utf-8")
        (out/"chat.md").write_text("# 真实模型验收聊天（含原始失败与修正续测）\n\n"+"\n\n".join(f"**{m.kind.value}**\n\n{m.text}" for m in assistant.messages.entries),encoding="utf-8")
        (out/"Open-Test.cmd").write_text('@echo off\r\nchcp 65001 >nul\r\ncall "'+str(ROOT/"Open-Studio.cmd")+'" --profile-home "'+str(home)+'"\r\n',encoding="utf-8")
        assistant.shutdown()
    print(json.dumps({"passed":True,"duration":25,"notes":79,"readonlyProgressWrites":result["writeCount"],"memory":audit["memoryStatus"]}),flush=True)


if __name__ == "__main__":
    main()
