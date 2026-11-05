"""Evidence-based, optional end-of-turn feedback; never a song-specific workflow."""
from __future__ import annotations

import hashlib
import json


class RecoveryReview:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.status = ''
        self.last_success = True
        self.evidence = set()
        self.reviewed = set()

    @staticmethod
    def signature(name, arguments):
        return hashlib.sha256((name + '\n' + json.dumps(arguments, sort_keys=True,
            ensure_ascii=False, default=str)).encode()).hexdigest()

    def observe(self, name, arguments, result):
        if name == 'report_progress':
            if result.success:
                status = arguments.get('status', self.status)
                if isinstance(status, str):
                    self.status = status
            return
        if name in {'workspace_info', 'inspect_app'}:
            return
        self.last_success = result.success
        exit_code = result.data.get('exit_code')
        self.evidence.add((self.signature(name, arguments), result.success, result.error_code,
                           str(result.data.get('status', '')), exit_code if isinstance(exit_code, int) else None))

    def feedback(self, pending_commands=()):
        unresolved = self.status in {'blocked', 'needs_input'} or not self.last_success or bool(pending_commands)
        signature = (frozenset(self.evidence), tuple(sorted(pending_commands)))
        if not self.enabled or not unresolved or signature in self.reviewed:
            return None
        self.reviewed.add(signature)
        return (
            '应用的默认任务收尾复核（可在设置关闭）：你刚才报告任务受阻、工具失败，或仍有命令未结束。'
            '请结合实际工具回执，复核这是单一路径失败，还是确实缺少只能由用户提供的信息。'
            '仍可使用联网检索、可视曲谱、代码、命令和自建工具。若有可行路径，请自行继续完成用户目标；'
            '若确实无法继续，请具体说明剩余阻碍。不要把未使用的途径说成已尝试过，不要换成用户没要求的作品。'
            '由你决定方案；相同执行证据不会反复触发复核。'
            + (' 未结束的本轮命令：' + ', '.join(pending_commands)
               + '。可用command_result取得真实退出结果，或明确停止它们；启动不等于完成。' if pending_commands else ''))
