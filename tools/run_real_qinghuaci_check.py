"""Opt-in live-model check using a read-only copy of the approved song opening."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from urllib.request import Request, urlopen

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QUICK_BACKEND", "software")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.assistant_controller import MusicAssistantController
from ai_piano_studio.music_agent.codex_provider import CodexProvider, detect_codex
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
from ai_piano_studio.music_agent.song_resolver import validate_midi
from ai_piano_studio.persistence.midi import materialize_midi_import, export_midi
from ai_piano_studio.persistence.project_document import load_project_document
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.recent_files import RecentFiles
from tools.studio_evidence import install_memory_output


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fields(note, *, identity=False):
    return ((note.id,) if identity else ()) + (
        note.pitch, round(note.start_time, 7), round(note.duration, 7), note.velocity,
        note.layer_id, note.instrument, str(note.release_mode), note.release_ms,
        note.track, note.channel,
    )


def prepare(source, out):
    if out.exists():
        raise SystemExit("Use a fresh evidence directory")
    source = source.resolve(strict=True)
    out.mkdir(parents=True)
    baseline_bytes = source.read_bytes()
    source_hash = hashlib.sha256(baseline_bytes).hexdigest()
    (out / "approved-opening.pianoai.json").write_bytes(baseline_bytes)
    baseline = load_project_document(out / "approved-opening.pianoai.json").state
    end = max(n.start_time + n.duration for n in baseline.active_notes)
    assert len(baseline.active_notes) == 69 and abs(end - 10) < 1e-6
    assert len(baseline.layers) == 2
    provenance = json.loads((source.parent / "music-source.json").read_text(encoding="utf-8"))
    write_json(out / "original-source.json", provenance)
    url = provenance["download_url"]
    assert url.startswith("https://s3-ap-southeast-1.amazonaws.com/music.bensonby.me/")
    with urlopen(Request(url, headers={"User-Agent": "Flowing-Piano/Acceptance"}), timeout=30) as response:
        assert response.url == url
        raw = response.read(8_000_001)
    assert len(raw) <= 8_000_000
    assert hashlib.sha256(raw).hexdigest() == provenance["sha256"]
    (out / "same-source-full.mid").write_bytes(raw)
    parsed = validate_midi(raw)
    full, layers = materialize_midi_import(parsed, baseline.tempo, status="saved")
    prefix = tuple(replace(n, duration=min(n.duration, end - n.start_time))
                   for n in full if n.start_time < end)
    assert Counter(map(fields, prefix)) == Counter(map(fields, baseline.active_notes)), "Source does not match approved opening"
    tail = tuple(replace(n, start_time=max(end, n.start_time),
                         duration=min(25, n.start_time + n.duration) - max(end, n.start_time))
                 for n in full if n.start_time < 25 and n.start_time + n.duration > end)
    assert tail and max(n.start_time + n.duration for n in tail) >= 24.95
    route_ids = [layer.id for layer in baseline.layers]
    # The source adds a vocal melody route after 16.75s. Render those notes
    # on the existing upper piano staff; keep both approved project layers.
    route_map = {layer.id: layer.id for layer in baseline.layers}
    upper = max(baseline.layers, key=lambda layer: sum(n.pitch for n in baseline.active_notes if n.layer_id == layer.id)
                / sum(n.layer_id == layer.id for n in baseline.active_notes))
    for layer in layers:
        route_map.setdefault(layer.id, upper.id)
    tail = tuple(replace(n, layer_id=route_map[n.layer_id]) for n in tail)
    # Compact integer tick reference fits a bounded request without changing note data.
    rows = [[n.pitch, round(n.start_time * parsed.ppq * baseline.tempo.bpm / 60),
             round(n.duration * parsed.ppq * baseline.tempo.bpm / 60),
             n.velocity, route_ids.index(n.layer_id)] for n in tail]
    reference = dict(ppq=parsed.ppq, bpm=baseline.tempo.bpm, layers=route_ids,
                     columns=["pitch", "absoluteStartTick", "durationTicks", "velocity", "layerIndex"], notes=rows)
    write_json(out / "following-reference.json", reference)
    export_midi(out / "approved-opening.mid", baseline.active_notes, baseline.tempo, layers=baseline.layers)
    export_midi(out / "expected-source-25s.mid", (*baseline.active_notes, *tail), baseline.tempo, layers=baseline.layers)
    evidence = dict(model="gpt-6.1-sol", sourceProject=str(source), sourceSha256=source_hash,
        baselineNoteCount=69, baselineDuration=10, sourceMidiSha256=provenance["sha256"],
        openingMatchesSameMidi=True, followingReferenceNoteCount=len(tail),
        referencePreparation="read-only download and parsing before the real model; not model source-discovery acceptance",
        sourceEdition="User-approved opening's third-party MIDI edition; not verified against the official recording",
        reference=reference, sourceRouteToProjectLayer=route_map, turns=[])
    write_json(out / "evidence.json", evidence)
    assert sha(source) == source_hash
    print(json.dumps({k: evidence[k] for k in ("baselineNoteCount", "openingMatchesSameMidi", "followingReferenceNoteCount")}), flush=True)


def run(out):
    evidence = json.loads((out / "evidence.json").read_text(encoding="utf-8"))
    source = Path(evidence["sourceProject"])
    assert sha(source) == evidence["sourceSha256"]
    baseline = load_project_document(out / "approved-opening.pianoai.json").state
    home = out / "profile"
    assert not home.exists(), "Never overwrite a prior live run"
    home.mkdir()
    app = QApplication([])
    app.setOrganizationName("AIPianoStudio")
    app.setApplicationName("AI Piano Studio Rebuild")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope, str(home / "settings"))
    assert Path(QSettings().fileName()).resolve().is_relative_to(home)
    blocked_audio = install_memory_output()

    class Recorder(CodexProvider):
        def stream_events(self, messages, tools, reasoning, attachments, **kwargs):
            with (out / "model-requests.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(model=self.model_id, messages=list(messages), tools=list(tools)), ensure_ascii=False) + "\n")
            for event in super().stream_events(messages, tools, reasoning, attachments, **kwargs):
                with (out / "model-responses.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(asdict(event), ensure_ascii=False, default=str) + "\n")
                yield event

    info = detect_codex()
    assert info.get("supported") and info.get("login") == "chatgpt", info
    model = evidence["model"]
    models = CodexProvider(model).list_models()
    assert model in models
    settings = ProviderSettingsService(settings=QSettings(), credentials=MemoryCredentialStore(),
        provider_factory=lambda _id: Recorder(model, timeout_seconds=600))
    settings.update_codex_status({**info, "checked": True})
    settings.set_dynamic_models("codex", models)
    recent = RecentFiles(QSettings(str(home / "settings" / "recent.ini"), QSettings.IniFormat))
    editor = EditorController(baseline, project_library=ProjectLibrary(home / "Projects"), recent_files=recent)
    editor.setAutosavePolicy(False, 60)
    assert editor.saveNamedProject("青花瓷－原开头续写25秒－真实模型验收")

    def pump(predicate, timeout=1800):
        deadline, last = time.monotonic() + timeout, 0
        while not predicate():
            app.processEvents()
            time.sleep(.01)
            if time.monotonic() >= deadline:
                raise TimeoutError("Owned live test timed out")
            if assistant_ref and time.monotonic() - last >= 20:
                a = assistant_ref[0]
                print(json.dumps(dict(stage=a.taskProgress.get("stage"), elapsed=a.taskProgress.get("elapsedSeconds"),
                    notes=len(editor.state.active_notes)), ensure_ascii=False), flush=True)
                last = time.monotonic()
        app.processEvents()

    assistant_ref = []
    pump(lambda: not editor.isSaving, 30)
    path = Path(editor.currentFilePath)
    shutil.copy2(out / "original-source.json", path.parent / "music-source.json")
    shutil.copy2(source.parent / "MUSIC-SOURCE.txt", path.parent / "MUSIC-SOURCE.txt")
    assistant = MusicAssistantController(editor, provider_settings=settings,
        history_path=home / "assistant" / "conversation.sqlite3", automatic_music=True)
    assistant_ref.append(assistant)
    assistant._on_codex_detection_finished(info)
    settings.set_dynamic_models("codex", models)
    assert assistant.selectProvider("codex") and assistant.selectModel(model)
    assert assistant.currentModelStatus["canCreate"]
    assistant.setCodexWaitSeconds(600)
    evidence.update(projectPath=str(path), profileHome=str(home), startedAt=datetime.now(timezone.utc).isoformat())
    compact = json.dumps(evidence["reference"], ensure_ascii=False, separators=(",", ":"))
    instruction = ("这是周杰伦《青花瓷》，当前10秒69音符的开头是用户认可的。按同一原曲版本往下接，写到25秒，"
        "不要重复开头，不要改动任何已有音符和声部。只添加10秒及之后的音符，请实际完成并直接修改进去。"
        "以下SOURCE_QHC是程序从原工程记录的同一MIDI只读下载解析的后续参考，已验证SHA256及69音符开头一致；"
        "这是该网络编配版本的依据，不声称官方录音验证。按表准确转为秒，seconds=ticks*60/(bpm*ppq)，"
        "layerIndex查layers。不要改编，不要猜写；请按所有参考音符写入。若本条被裁剪，请先用get_task_history"
        "查询SOURCE_QHC并用char_offset读取完整表。\nSOURCE_QHC=" + compact
        + "\n请核对表中全部后续音符，保留开头，真正写到25秒。SOURCE_QHC_END")
    evidence["initialInstructionBytes"] = len(instruction.encode("utf-8"))

    def snapshot(label):
        editor.synchronizeNow()
        evidence["turns"].append(dict(label=label, result=assistant.lastTurnResult,
            progress=assistant.taskProgress, duration=editor.state.playback_duration,
            notes=len(editor.state.active_notes), task=deepcopy(assistant._task_memory.state)))
        write_json(out / "evidence.json", evidence)
        write_json(out / "chat.json", [m.to_dict() for m in assistant.messages.entries])
        (out / "chat.md").write_text("# 青花瓷真实模型测试聊天\n\n" + "\n\n".join(
            f"**{m.kind.value}**\n\n{m.text}" for m in assistant.messages.entries), encoding="utf-8")

    try:
        assert assistant.sendMessage(instruction)
        began = time.monotonic()
        pump(lambda: not assistant.isBusy or assistant.taskProgress.get("stage") in {"submitted", "responding"}
             or time.monotonic() - began >= 8, 30)
        evidence["hiInsertedWhileBusy"] = assistant.isBusy
        assert assistant.isBusy, "No in-flight task in which to insert hi"
        assert assistant.sendMessage("hi")
        pump(lambda: not assistant.isBusy)
        snapshot("continuation_with_hi")
        assert assistant.sendMessage("刚才让你按周杰伦《青花瓷》原曲续写到25秒、不要重复开头。现在进度怎么样？"
            "请读取当前工程核实实际时长和音符，确认有没有保留原来的69个音符。不要重新创作，也不要因为中途的hi忘掉原任务。")
        pump(lambda: not assistant.isBusy)
        snapshot("progress_query")
    finally:
        if assistant.isBusy:
            assistant.cancelGeneration()
            pump(lambda: not assistant.isBusy, 30)
        editor.synchronizeNow()
        editor.saveCurrent()
        pump(lambda: not editor.isSaving, 30)
        snapshot("saved")
        saved = load_project_document(path).state
        by_id = {n.id: n for n in saved.active_notes}
        opening_ok = all(n.id in by_id and fields(n, identity=True) == fields(by_id[n.id], identity=True)
                         for n in baseline.active_notes)
        old_ids = {n.id for n in baseline.active_notes}
        added = [n for n in saved.active_notes if n.id not in old_ids]
        # Match the source in MIDI-relevant fields. Original route/channel are
        # editor import metadata, not add_notes arguments.
        music = lambda n: (n.pitch, round(n.start_time, 6), round(n.duration, 6), n.velocity, n.layer_id)
        ref = evidence["reference"]
        expected = Counter((r[0], round(r[1]*60/(ref["bpm"]*ref["ppq"]), 6),
                            round(r[2]*60/(ref["bpm"]*ref["ppq"]), 6), r[3], ref["layers"][r[4]]) for r in ref["notes"])
        actual = Counter(map(music, added))
        checks = dict(opening69Unchanged=opening_ok, originalFileShaUnchanged=sha(source)==evidence["sourceSha256"],
            savedDurationSeconds=saved.playback_duration, savedNotes=len(saved.active_notes), addedNotes=len(added),
            addedAtOrAfter10=all(n.start_time >= 10 for n in added),
            exactFollowingSourceMatch=actual == expected, missingReferenceNotes=sum((expected-actual).values()),
            unexpectedAddedNotes=sum((actual-expected).values()), physicalAudioAttempts=blocked_audio,
            modelRequests=sum(1 for _ in (out / "model-requests.jsonl").open(encoding="utf-8")))
        evidence["checks"] = checks
        write_json(out / "evidence.json", evidence)
        export_midi(out / "result.mid", saved.active_notes, saved.tempo, layers=saved.layers)
        (out / "Open-Test.cmd").write_text('@echo off\r\nchcp 65001 >nul\r\ncall "' + str(ROOT / "Open-Studio.cmd")
            + '" --profile-home "' + str(home) + '"\r\n', encoding="utf-8")
        print(json.dumps(checks, ensure_ascii=False), flush=True)
        assistant.shutdown()
    return 0 if opening_ok and actual == expected and checks["originalFileShaUnchanged"] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    out = args.output.resolve()
    if args.prepare_only:
        assert args.source is not None
        prepare(args.source, out)
        return 0
    return run(out)


if __name__ == "__main__":
    raise SystemExit(main())
