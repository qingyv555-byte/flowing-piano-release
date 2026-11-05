from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.hub_workflow_report import read_results, render_report


@pytest.mark.parametrize("skipped,expected", [(True, 1), (False, 0)])
def test_runner_exit_code_requires_every_expected_check_to_pass(tmp_path, monkeypatch, skipped, expected):
    from tools import hub_workflow_report as workflow
    monkeypatch.setattr(workflow, "ROOT", tmp_path)
    monkeypatch.setattr(workflow, "REPORT_DIR", tmp_path / "report")

    def run(command, **kwargs):
        if command[0] == "git":
            return SimpleNamespace(stdout="fixture", returncode=0)
        cases = "".join(
            f'<testcase name="{step[2].rsplit("::", 1)[-1]}">' +
            ('<skipped/>' if skipped else '') + '</testcase>'
            for step in workflow.STEPS
        )
        (workflow.REPORT_DIR / "results.xml").write_text(f'<testsuite>{cases}</testsuite>')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(workflow.subprocess, "run", run)
    assert workflow.main() == expected


def test_report_marks_failed_and_missing_steps_without_claiming_success(tmp_path: Path):
    xml = tmp_path / "results.xml"
    xml.write_text('''<testsuites><testsuite><testcase name="test_save_io_failure_keeps_dirty_music_and_file_and_shows_error"><failure message="disk full">trace here</failure></testcase></testsuite></testsuites>''', encoding="utf-8")

    report = render_report(read_results(xml), 1, "abc123")

    assert "发现失败或未完成的检查" in report
    assert "0/7 项通过" in report
    assert "disk full" in report
    assert "trace here" in report
    assert "未运行" in report


def test_report_escapes_test_failure_text(tmp_path: Path):
    xml = tmp_path / "results.xml"
    xml.write_text('''<testsuite><testcase name="test_save_io_failure_keeps_dirty_music_and_file_and_shows_error"><failure message="&lt;script&gt;bad&lt;/script&gt;" /></testcase></testsuite>''', encoding="utf-8")

    report = render_report(read_results(xml), 1, "abc123")

    assert "&lt;script&gt;bad&lt;/script&gt;" in report
    assert "<script>bad</script>" not in report


def test_pending_report_clears_previous_green_status():
    report = render_report({}, 0, "abc123", pending=True)

    assert "本次诊断尚未完成" in report
    assert "0/7 项通过" in report
    assert "等待结果" in report
