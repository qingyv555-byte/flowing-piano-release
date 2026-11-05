"""Run source-backed Studio checks and publish an honest local workflow report."""
from __future__ import annotations

import html
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "docs" / "hub"
STEPS = (
    ("启动", "窗口与后台服务", "tests/qt/test_app_startup.py::test_deferred_startup_services_attach_after_the_first_window", "src/ai_piano_studio/studio.py"),
    ("创作", "创建音符与撤销", "tests/qt/test_studio_shell.py::test_studio_create_selection_and_undo_use_real_editor", "src/ai_piano_studio/editor/controller.py"),
    ("作品", "保存并重新打开", "tests/qt/test_studio_workflows.py::test_save_rename_copy_home_continue_recent_open_actual_documents", "src/ai_piano_studio/persistence/project_document.py"),
    ("播放", "进度与声音控制", "tests/qt/test_studio_workflows.py::test_transport_seek_music_and_sound_controls_change_real_state", "src/ai_piano_studio/audio/controller.py"),
    ("导出", "MIDI、WAV 与工程文件", "tests/qt/test_studio_workflows.py::test_new_file_menu_exports_real_midi_wav_and_project", "src/ai_piano_studio/persistence/midi.py"),
    ("助手", "提案应用与撤销", "tests/qt/test_studio_workflows.py::test_assistant_new_entry_stages_applies_and_undoes_local_proposal", "src/ai_piano_studio/music_agent/proposal_manager.py"),
    ("异常", "保存失败时保留作品", "tests/qt/test_studio_workflows.py::test_save_io_failure_keeps_dirty_music_and_file_and_shows_error", "src/ai_piano_studio/editor/controller.py"),
)


def read_results(xml_path: Path) -> dict[str, tuple[str, str]]:
    """Missing test cases remain unknown; they must never be presented as passing."""
    if not xml_path.exists():
        return {}
    cases: dict[str, tuple[str, str]] = {}
    for case in ElementTree.parse(xml_path).iter("testcase"):
        name = case.get("name", "")
        finding = case.find("failure")
        if finding is None:
            finding = case.find("error")
        if finding is not None:
            cases[name] = ("failed", (finding.get("message", "") + "\n" + (finding.text or "")).strip())
        elif case.find("skipped") is not None:
            cases[name] = ("skipped", "测试被跳过")
        else:
            cases[name] = ("passed", "")
    return cases


def render_report(results: dict[str, tuple[str, str]], exit_code: int, revision: str,
                  *, pending: bool = False) -> str:
    labels = {"passed": "通过", "failed": "失败", "skipped": "跳过", "unknown": "未运行"}
    rows = []
    passed = 0
    for index, (phase, title, test, source) in enumerate(STEPS, 1):
        status, details = results.get(test.rsplit("::", 1)[-1], ("unknown", "未收到这个测试的结果"))
        passed += status == "passed"
        detail = html.escape(details[:6000])
        rows.append(
            f'<li class="step {status}"><details><summary>'
            f'<span class="number">{index:02d}</span><span class="name"><b>{html.escape(phase)}</b><small>{html.escape(title)}</small></span>'
            f'<span class="status">{labels[status]}</span></summary><div class="detail">'
            f'<p>测试：<code>{html.escape(test)}</code></p><p>相关源码：<code>{html.escape(source)}</code></p>'
            f'{f"<pre>{detail}</pre>" if detail else "<p>该检查通过。实际桌面显示和硬件听感仍需人工确认。</p>"}'
            f'</div></details></li>'
        )
    overall = ("本次诊断尚未完成" if pending else
               "诊断通过" if exit_code == 0 and passed == len(STEPS) else
               "发现失败或未完成的检查")
    when = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    exit_text = "等待结果" if pending else f"测试进程退出码 {exit_code}"
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Flowing Piano 测试流程</title><style>
:root{{color-scheme:dark;font-family:system-ui,"Microsoft YaHei",sans-serif;background:#111c26;color:#e9f1f2}}
*{{box-sizing:border-box}}body{{margin:0;padding:24px;max-width:900px;margin:auto}}h1{{font-size:1.45rem;margin:0 0 8px}}p{{line-height:1.6}}.hint{{color:#acc0c7;font-size:.9rem}}.summary{{background:#1e3340;border:1px solid #47606b;border-radius:14px;padding:15px;margin:20px 0}}.summary b{{color:#e2f1f0}}ol{{list-style:none;margin:0;padding:0}}.step{{position:relative;margin:0 0 21px;padding-left:22px}}.step:not(:last-child):before{{content:"↓";position:absolute;left:7px;bottom:-22px;color:#8ba4ad;font-size:22px}}details{{background:#1b2a35;border:1px solid #536a72;border-left:5px solid #879ca3;border-radius:12px}}.passed details{{border-left-color:#53c49a}}.failed details{{border-left-color:#f27979}}.skipped details{{border-left-color:#e7ba69}}summary{{cursor:pointer;display:flex;align-items:center;gap:12px;padding:14px;list-style:none}}summary::-webkit-details-marker{{display:none}}.number{{font-weight:700;color:#a4c4c8}}.name{{display:flex;flex:1;flex-direction:column;gap:3px}}small{{color:#b4c5ca}}.status{{font-weight:700}}.passed .status{{color:#78dbb4}}.failed .status{{color:#ff9d9d}}.skipped .status{{color:#f3d087}}.detail{{border-top:1px solid #536a72;padding:4px 15px 15px;font-size:.85rem;overflow-wrap:anywhere}}code,pre{{font-family:Consolas,monospace}}pre{{white-space:pre-wrap;background:#0f1b23;padding:12px;border-radius:8px;max-height:280px;overflow:auto}}.footer{{margin-top:30px;color:#a8bdc2;font-size:.82rem}}
</style></head><body><h1>Flowing Piano · 测试流程</h1><p class="hint">基于真实 Qt 自动化测试的最近一次结果；展开节点可看测试、相关源码和失败摘要。红色定位到失败的检查，不等于自动证明根因。</p><div class="summary"><b>{overall}</b><br>{passed}/{len(STEPS)} 项通过 · {exit_text} · 主线提交 {html.escape(revision)}<br>生成时间 {html.escape(when)}</div><ol>{''.join(rows)}</ol><p class="footer">灰色表示没有收到结果；绿色只表示对应自动测试通过。图表不会监控正在运行的应用，也不能代替可见界面与实体音频验收。重新运行 npm run check:workflow 后，在 Hub 点“查看图表”刷新。</p></body></html>'''


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    xml_path = REPORT_DIR / "results.xml"
    xml_path.unlink(missing_ok=True)
    revision = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              text=True, capture_output=True, check=False).stdout.strip() or "unknown"
    output = REPORT_DIR / "workflow.html"
    staged = REPORT_DIR / "workflow.html.tmp"
    staged.write_text(render_report({}, 0, revision, pending=True), encoding="utf-8")
    staged.replace(output)
    temporary = ROOT / ".pytest-tmp" / "hub-workflow"
    temporary.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(TEMP=str(temporary), TMP=str(temporary), QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software")
    command = [sys.executable, str(ROOT / "tools" / "run_silent_tests.py"),
               "-q", f"--junitxml={xml_path}", *(step[2] for step in STEPS)]
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    try:
        results = read_results(xml_path)
    except ElementTree.ParseError as error:
        print(f"Cannot parse JUnit results: {error}", file=sys.stderr)
        results = {}
    staged.write_text(render_report(results, completed.returncode, revision), encoding="utf-8")
    staged.replace(output)
    print(f"Workflow diagram updated: {output}")
    print(f"Test process exit code: {completed.returncode}")
    all_passed = all(
        results.get(step[2].rsplit("::", 1)[-1], ("unknown", ""))[0] == "passed"
        for step in STEPS
    )
    return completed.returncode or (0 if all_passed else 1)


if __name__ == "__main__":
    raise SystemExit(main())
