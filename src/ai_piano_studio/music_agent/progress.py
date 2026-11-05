"""Safe execution events and a Qt-owned, generation-scoped task receipt.

Provider logs, message bodies and reasoning never enter this model. Elapsed
time is a clock, not evidence that any musical stage has been completed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Signal


STAGES = {
    'source_confirmation': '已找到候选，等待确认歌曲与版本',
    "preparing": "正在准备 AI 服务",
    "authenticating": "正在检查模型连接及登录状态",
    "starting": "正在启动模型会话",
    "connecting": "正在连接模型服务",
    "submitted": "请求已发送，正在等待模型响应",
    "understanding": "正在理解音乐需求",
    "searching": "正在检索歌曲及音乐资源",
    "searching_online": "正在联网查找曲谱与 MIDI 来源",
    "parsing": "正在获取或解析曲谱",
    "transcribing": "正在转换音频、识别音符",
    "source_failed": "此来源未取得可用音符，继续检索或创作",
    "generating": "正在生成旋律与钢琴伴奏",
    "responding": "已收到模型响应，正在处理",
    "reading": "正在读取音乐工程",
    "writing": "正在写入音乐工程",
    "verifying": "正在验证与准备试听",
    "review": "音符已准备，可先试听再应用",
    "preview": "正在启动试听",
    "completed": "已完成",
    "responded": "本轮回复结束",
    "failed": "任务失败",
    "cancelled": "已取消",
}
SAFE_ERRORS = {
    'unfinished_commands': '仍有本轮命令未结束；本轮收尾已停止这些命令，不能将其视为已完成。',
    'source_confirmation_required': '需要先核对并确认歌曲来源，当前工程尚未改动。',
    'resource_unavailable': '相同资源操作连续失败，尚未完成；可以改用其他来源、页码或格式继续。',
    "timeout": "等待已达到本次设置的上限，可以重试或增加等待时限。",
    "authentication": "登录或认证不可用，请检查当前服务连接。",
    "rate_limit": "服务额度或请求频率受限，请稍后重试。",
    "provider_unavailable": "模型服务不可用，请检查网络和连接。",
    "model_unavailable": "当前模型的音乐工具尚未验证或不可用，请先验证或切换模型。",
    "configuration_required": "AI 服务尚未配置完整，请在连接设置中保存服务地址、模型及认证信息。",
    "no_write": "模型没有生成实际音符，本次创作未完成。",
    "incomplete_music": "模型生成的片段未达到请求时长，本次完整创作未完成，请重试。",
    "source_tempo_unconfirmed": "模型尚未确认谱面速度，本次还原未完成，尚未准备或应用作品。请重试。",
    "score_images_unsupported": "谱面图片已取得，但当前模型连接无法读取这类图片，尚未生成试听。",
    "score_image_too_small": "拿到的是低分辨率谱图，无法可靠转写音符。需要同一版本的高清谱面。",
    "score_transcription_incomplete": "谱图已送交模型连接，音符转写未完成，尚未生成试听。",
    "conflict": "工程内容已经变化，本次结果未应用，请重试。",
    "needs_input": "任务需要补充信息，尚未生成完整作品。",
    "source_unavailable": "本轮尚未取得可用音乐资料，尚未准备或应用作品。可让助手继续尝试其他来源或格式。",
    "style_unavailable": "本轮样式未应用，现有外观或设置草稿已保留。",
    "search_unavailable": "本次联网检索未能完成，尚未取得可用曲谱。可以重试。",
    "transcription_unavailable": "本机转谱模型尚不可用。",
    "provider_error": "模型请求未完成，请检查连接后重试。",
    "execution_error": "音乐处理未完成，工程保留原状，请重试。",
    "context_error": "准备音乐数据失败，请求尚未发送给模型，工程未更改。",
    "context_too_large": "本次请求累计数据超过连接容量，尚未完成试听；可分段处理后继续。",
    "native_output_limit": "模型连接返回的数据超过处理容量，本次任务未完成。",
}
SOURCE_DETAILS = {
    "runtime_unavailable": "可选曲谱／转谱组件尚不可用，继续其他来源",
    "transcription_failed": "音频转谱未成功，继续其他来源",
    "network_unavailable": "曲库连接未成功，继续其他来源",
    "source_rejected": "音乐资料未通过验证，继续其他来源",
    "score_ocr_unavailable": "尚未接入 PDF 图片识谱，继续其他来源",
}


@dataclass(frozen=True, slots=True)
class ProviderProgressEvent:
    stage: str
    status: str = "running"
    current: int | None = None
    total: int | None = None
    process_alive: bool | None = None
    response_received: bool = False
    detail_code: str = ""


class TaskProgress(QObject):
    changed = Signal()

    def __init__(self, parent: QObject | None = None, *, clock=time.monotonic, diagnostic_path: Path | None = None):
        super().__init__(parent)
        self._clock = clock
        self._started = 0.0
        self._task: dict = {}
        self._steps: list[dict] = []
        self.diagnostic_path = diagnostic_path
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.tick)

    def start(self, generation: int, title: str, provider: str, model: str) -> None:
        self._started = self._clock()
        now = datetime.now(timezone.utc).isoformat()
        self._steps = []
        self._task = dict(taskId=uuid4().hex, generationId=generation,
                          title=title[:240], providerId=provider, modelId=model,
                          startedAt=now, updatedAt=now, elapsedSeconds=0,
                          status="running", stage="", stageLabel="",
                          progressCurrent=None, progressTotal=None,
                          processAlive=None, responseReceived=False,
                          lastVerifiedActivity="", safeErrorCode="", safeErrorMessage="")
        self._timer.start()
        self.record(generation, ProviderProgressEvent("preparing"))

    def clear(self) -> None:
        self._timer.stop()
        self._task = {}
        self._steps = []
        self.changed.emit()

    def set_model_driven(self, generation):
        if self._task.get('generationId') == generation:
            self._task.update(modelDriven=True, modelSteps=[], modelMessage='', modelStatus='')
            self.changed.emit()

    def report_model(self, generation, payload):
        if self._task.get('generationId') != generation:
            return
        if 'title' in payload:
            self._task['title'] = str(payload['title'])
        if 'message' in payload:
            self._task['modelMessage'] = str(payload['message'])
        if 'status' in payload:
            self._task['modelStatus'] = str(payload['status'])
        if 'steps' in payload:
            self._task['modelSteps'] = [dict(row) for row in payload['steps']]
        self._task['modelDriven'] = True
        self.changed.emit()

    def set_waiting_for_user(self, generation, waiting):
        if self._task.get('generationId') == generation:
            self._task['waitingForUser'] = bool(waiting)
            self.changed.emit()

    def record(self, generation: int, event: ProviderProgressEvent) -> None:
        if (not self._task or generation != self._task["generationId"]
                or self._task["status"] not in {"running", "waiting"}
                or not isinstance(event, ProviderProgressEvent)):
            return
        if event.stage != "heartbeat" and event.stage not in STAGES:
            return
        before = (self._task["stage"], self._task["stageLabel"], self._task["status"],
                  self._task["progressCurrent"], self._task["progressTotal"],
                  self._task["processAlive"], self._task["responseReceived"])
        now = datetime.now(timezone.utc).isoformat()
        if event.process_alive is not None:
            self._task["processAlive"] = event.process_alive
        self._task["responseReceived"] |= event.response_received
        self._task["lastVerifiedActivity"] = now
        self._task["updatedAt"] = now
        if event.stage != "heartbeat":
            label = SOURCE_DETAILS.get(event.detail_code, STAGES[event.stage]) if event.stage == "source_failed" else STAGES[event.stage]
            if self._task["stage"] != event.stage or self._task["stageLabel"] != label:
                if self._steps and self._steps[-1]["status"] in {"running", "waiting"}:
                    self._steps[-1]["status"] = "completed"
                self._steps.append(dict(stage=event.stage, label=label,
                                        status=event.status, at=now, detailCode=event.detail_code if event.detail_code in SOURCE_DETAILS else ""))
                self._steps = self._steps[-40:]
            elif self._steps:
                self._steps[-1]["status"] = event.status
            self._task.update(stage=event.stage, stageLabel=label,
                              status=event.status if event.status in {"running", "waiting"} else "running")
            current, total = event.current, event.total
            if (isinstance(current, int) and isinstance(total, int)
                    and 0 <= current <= total and total > 0):
                self._task.update(progressCurrent=current, progressTotal=total)
            else:
                self._task.update(progressCurrent=None, progressTotal=None)
        after = (self._task["stage"], self._task["stageLabel"], self._task["status"],
                 self._task["progressCurrent"], self._task["progressTotal"],
                 self._task["processAlive"], self._task["responseReceived"])
        if before == after:
            # The 1 Hz clock remains live; repeated token/activity events do
            # not flood QML or the diagnostic file with identical snapshots.
            return
        if event.stage != "heartbeat":
            self._diagnose()
        self.tick()

    def await_review(self, generation: int) -> None:
        self.record(generation, ProviderProgressEvent("review", status="waiting", process_alive=False))
        self._timer.stop()

    def await_source_confirmation(self, generation: int) -> None:
        self.record(generation, ProviderProgressEvent('source_confirmation', status='waiting', process_alive=False))
        self._timer.stop()

    def finish(self, generation: int, status: str, *, error_code: str = "") -> None:
        if not self._task or self._task["generationId"] != generation:
            return
        if self._task["status"] not in {"running", "waiting"}:
            return
        stage = status if status in {"completed", "cancelled", "responded"} else "failed"
        if self._steps:
            self._steps[-1]["status"] = "completed" if stage in {"completed", "responded"} else stage
        self._task.update(status=stage, stage=stage, stageLabel=STAGES[stage],
                          safeErrorCode=error_code if error_code in SAFE_ERRORS else "execution_error" if error_code else "",
                          safeErrorMessage=SAFE_ERRORS.get(error_code, SAFE_ERRORS["execution_error"] if error_code else ""),
                          updatedAt=datetime.now(timezone.utc).isoformat())
        self._timer.stop()
        self._task["processAlive"] = False
        self._diagnose()
        self.tick()

    def _diagnose(self) -> None:
        """Bounded local stage receipts: no requests, filenames or model output."""
        if self.diagnostic_path is None:
            return
        keys = ("taskId", "generationId", "stage", "status", "updatedAt", "elapsedSeconds",
                "progressCurrent", "progressTotal", "processAlive", "responseReceived", "safeErrorCode")
        row = {key: self._task[key] for key in keys}
        row["sourceDetailCode"] = self._steps[-1].get("detailCode", "") if self._steps else ""
        row["elapsedSeconds"] = max(0, int(self._clock() - self._started))
        path = Path(self.diagnostic_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 256_000:
                path.replace(path.with_suffix(".previous.jsonl"))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")
        except OSError:
            logging.getLogger(__name__).warning("Task diagnostic receipt could not be stored")

    def tick(self) -> None:
        if self._task:
            self._task["elapsedSeconds"] = max(0, int(self._clock() - self._started))
            self.changed.emit()

    def snapshot(self) -> dict:
        if not self._task:
            return {}
        result = dict(self._task)
        result["steps"] = [dict(s) for s in self._steps]
        elapsed = result["elapsedSeconds"]
        result["elapsedText"] = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        hint = ""
        if result["status"] in {"running", "waiting"} and elapsed >= 30:
            if result["stage"] == "review":
                hint = "音符建议已准备好，等待应用。"
            elif result["processAlive"] is True and result["stage"] in {"transcribing", "parsing"}:
                hint = "音乐处理进程仍在运行，等待当前步骤完成。"
            elif result["processAlive"] is True:
                hint = "模型进程仍在运行；" + ("已收到过模型响应。" if result["responseReceived"] else "尚未收到模型响应。")
            else:
                hint = "请求仍未结束；" + ("已收到过模型响应。" if result["responseReceived"] else "尚未收到模型响应，连接状态等待确认。")
        result["waitHint"] = hint
        return result
