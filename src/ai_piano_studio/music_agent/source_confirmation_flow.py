"""Qt-thread-owned user decisions for a bounded song-source proposal."""
from dataclasses import dataclass
import re

from ai_piano_studio.music_agent.source_confirmation import SourceProposal, requested_excerpt_seconds
from ai_piano_studio.music_agent.song_resolver import corrected_song_title


@dataclass(frozen=True, slots=True)
class PendingSongSource:
    proposal: SourceProposal
    instruction: str
    baseline: tuple
    reference: object = None
    model_driven: bool = False
    task_id: str = ''


class SongSourceConfirmationFlow:
    def __init__(self, owner):
        self.owner = owner
        self.pending = None
        self.approved_generation = None
        self.approved_source = None
        self.rejected_sources = []
        self.confirmed = None

    def baseline(self):
        editor = self.owner._editor
        return (editor.projectSessionId, editor.projectRevision)

    def public(self):
        pending = self.pending
        if pending is None or pending.baseline != self.baseline():
            return {}
        try:
            duration = requested_excerpt_seconds(pending.instruction)
        except ValueError:
            return {}
        return dict(pending.proposal.public(), durationSeconds=duration)

    def validate_duration(self, instruction):
        try:
            requested_excerpt_seconds(instruction)
        except ValueError as error:
            had_pending = self.pending is not None
            self.clear()
            if had_pending:
                self.owner._task_progress.finish(self.owner._generation, 'failed', error_code='validation_error')
            self.owner._append_notice(str(error))
            self.owner.changed.emit()
            return False
        return True

    def approved_for(self, generation):
        return self.approved_source if generation == self.approved_generation else None

    def revoke_approval(self):
        self.approved_generation = self.approved_source = None

    def clear(self):
        self.pending = None
        self.confirmed = None
        self.revoke_approval()

    def offer(self, generation, proposal, instruction, baseline, reference=None, *, model_driven=False):
        o = self.owner
        if generation != o._active_generation or baseline != self.baseline() or o._shutting_down:
            return False
        if not self.validate_duration(instruction):
            o._task_progress.finish(generation, 'failed', error_code='validation_error')
            o._creation_workflow._retire(error=True)
            o.changed.emit()
            return False
        self.clear()
        task_id = o._task_memory.state.get('taskId', '') if model_driven else ''
        self.pending = PendingSongSource(proposal, instruction, baseline, reference, model_driven, task_id)
        if model_driven:
            o._memory_record('source_confirmation', {'source': self._source_facts(proposal, 'awaiting_confirmation')})
        o._last_turn_result = dict(status='source_confirmation', intent='CREATE_MUSIC', committed=False,
                                  noteCount=0, source=proposal.public())
        lines = [f'找到候选：《{proposal.title}》 · {proposal.artist or "歌手待核对"}',
                 f'版本：{proposal.edition}；段落：{proposal.section}；计划约 {requested_excerpt_seconds(instruction):g} 秒。',
                 '选定乐谱资源：' + proposal.selected_url,
                 '查找依据：' + proposal.summary]
        for evidence in proposal.evidence:
            lines.append('已读取来源：' + evidence.url)
        if proposal.uncertainty:
            lines.append('仍需核对：' + proposal.uncertainty)
        lines.append('请先确认是不是你要的这首歌；确认前不会生成音符或改动当前工程。')
        o._creation_reply('\n'.join(lines))
        o._task_progress.await_source_confirmation(generation)
        o._creation_workflow._retire()
        o.changed.emit()
        return True

    @staticmethod
    def _source_facts(proposal, stage):
        return dict(stage=stage, title=proposal.title, artist=proposal.artist, edition=proposal.edition,
                    section=proposal.section, source_url=proposal.selected_url, sha256=proposal.selected_sha256)

    def _start(self, instruction, display_text, *, approval=None, reference=None, model_driven=False):
        o = self.owner
        if o._shutting_down or o.isBusy:
            return False
        if not self.validate_duration(instruction):
            return False
        self.clear()
        o._generation += 1
        generation = o._generation
        o._retry_instruction = instruction
        o._retry_attachments = ()
        o._retry_allow_creative = False
        o._task_message_start_row = len(o._messages.entries)
        o._task_progress.start(generation, o._safe_text(display_text), o._provider_id, o._model_id)
        if approval is not None:
            self.approved_generation, self.approved_source = generation, approval
        if model_driven and approval is not None:
            self.confirmed = PendingSongSource(approval, instruction, self.baseline(), reference, True,
                                               o._task_memory.state.get('taskId', ''))
            o._memory_record('source_confirmed', {'source': self._source_facts(approval, 'confirmed')})
        else:
            o._memory_accept(instruction, 'CREATE_MUSIC', new_task=False)
        task = None
        if model_driven and approval is not None:
            task = dict(task_id=o._task_memory.state.get('taskId', ''), title=approval.title,
                        artist=approval.artist, section=approval.section, start_seconds=approval.start_seconds,
                        duration_seconds=requested_excerpt_seconds(instruction), status='source_confirmed')
        o._creation_workflow.start(generation, instruction, display_text=display_text,
                                  approved_source=approval, resolved_source=reference, song_task=task)
        return True

    def confirm(self, display_text='就是这首，继续'):
        pending = self.pending
        if (pending is None or pending.baseline != self.baseline() or self.owner._shutting_down
                or (pending.model_driven and pending.task_id != self.owner._task_memory.state.get('taskId'))):
            self.clear()
            self.owner.changed.emit()
            return False
        return self._start(pending.instruction, display_text,
                           approval=pending.proposal, reference=pending.reference, model_driven=pending.model_driven)

    def retry_confirmed(self, text):
        confirmed = self.confirmed
        if (confirmed is None or confirmed.baseline != self.baseline()
                or confirmed.task_id != self.owner._task_memory.state.get('taskId')
                or self.owner._task_memory.state.get('status') != 'failed'):
            return False
        return self._start(confirmed.instruction, text, approval=confirmed.proposal,
                           reference=confirmed.reference, model_driven=True)

    def reject(self, correction='不是这首，请重新寻找并核对其他候选'):
        pending = self.pending
        if pending is None or pending.baseline != self.baseline() or self.owner._shutting_down:
            self.clear()
            self.owner.changed.emit()
            return False
        self.rejected_sources = [*self.rejected_sources[-7:], pending.proposal]
        if pending.model_driven:
            self.clear()
            self.owner._memory_record('source_rejected', {'source': self._source_facts(pending.proposal, 'rejected')})
            return self.owner._send_message(correction, intent_template='')
        original = pending.instruction
        title = corrected_song_title(correction)
        if title:
            original = f'来一段{requested_excerpt_seconds(original):g}秒的《{title}》，取开头。'
        instruction = (original + '\n用户否定了上一候选，不能将其视为已确认：'
                       + pending.proposal.selected_url + '\n用户纠正：' + correction)
        return self._start(instruction, correction)

    def handle_text(self, text, *, model_driven=False):
        """Return None for a genuinely new request; status/greetings never approve."""
        clean = re.sub(r'[，,。！!\s]', '', text)
        refers_to_candidate = re.fullmatch(
            r'(?:对|是的|没错)?(?:就是这首|是这首|确认这首)(?:继续|继续生成|开始|吧)?', clean)
        if refers_to_candidate and (self.pending is None or self.pending.baseline != self.baseline()):
            if self.retry_confirmed(text):
                return True
            self.clear()
            self.owner._append_user_music_request(text)
            self.owner._append_notice('当前没有待确认的谱源。请重新查找，看到候选卡后再确认。')
            self.owner.changed.emit()
            return True
        if self.pending is None:
            return None
        if refers_to_candidate or (not model_driven and clean in {'对', '是的', '没错'}):
            return self.confirm(text)
        explicitly_rejected = re.match(r'^(?:这首不对|不是这首|不要这份|这个版本不对)(?:$|[，,。！!\s])', text)
        if (explicitly_rejected if model_driven
                else re.search(r'不是这首|不对|找错|换成|歌词|原唱|版本', text)):
            return self.reject(text)
        if clean in {'取消', '先取消', '不用了', '停止'}:
            self.owner._append_user_music_request(text)
            self.owner.cancelGeneration()
            return True
        if model_driven:
            return None
        if (clean in {'继续', '开始'}
                or re.search(r'找到.*[吗没？?]|找到什么|进度|怎么样|[hH][iI]|你好|在吗|是这首吗|对吗', text)):
            self.owner._append_user_music_request(text)
            self.owner._append_notice('已找到上方候选，正在等待你确认歌曲、版本和段落；当前工程没有改动。')
            self.owner.changed.emit()
            return True
        self.clear()
        self.rejected_sources.clear()
        return None
