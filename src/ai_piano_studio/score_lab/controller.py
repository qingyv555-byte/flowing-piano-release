"""Qt boundary for the independent, explicitly confirmed score experiment."""
from pathlib import Path
from threading import Event, Thread
from time import monotonic

from PySide6.QtCore import QObject, Property, Qt, QTimer, QUrl, Signal, Slot

from .contracts import LabFailure, duration, public_text, public_url
from .service import ScoreLabService
from ai_piano_studio.persistence.project_document import ProjectDocument, save_project_document


class ScoreLabController(QObject):
    changed = Signal()
    openRequested = Signal()
    _event = Signal(int, str, object)

    def __init__(self, home, playback, *, service=None, model_id='gpt-6.1-sol', assistant=None, parent=None):
        super().__init__(parent)
        self._home, self._playback = Path(home), playback
        self._service, self._model = service, model_id
        self._state, self._status, self._error, self._query = 'idle', '输入线索，先找谱源，再由你确认。', '', ''
        self._candidate = self._draft = None
        self._generation, self._thread, self._cancel = 0, None, Event()
        self._pending, self._closed = None, False
        self._rejected = []
        self._rejected_hashes, self._failures, self._live = [], [], []
        self._checked_sources = []
        self._failure, self._seconds, self._recovery_rounds = {}, 15, 0
        self._assistant = assistant
        self._active_candidate = None
        # A trusted click approves the exact song/version/section within one
        # user request, never a model suggestion or a future user correction.
        self._request_epoch, self._approval = 0, None
        self._started, self._elapsed = 0.0, 0
        self._preview_owned = self._starting = False
        self._preview_snapshot = []
        self._event.connect(self._receive, Qt.QueuedConnection)
        self._timer = QTimer(self, interval=250)
        self._timer.timeout.connect(self._tick)
        self._preview_timeout = QTimer(self, interval=15000, singleShot=True)
        self._preview_timeout.timeout.connect(self._preview_failed)
        if playback is not None:
            playback.playingChanged.connect(self._playing_changed)
            if hasattr(playback, 'audioFailed'):
                playback.audioFailed.connect(self._preview_failed)
            if hasattr(playback, 'detachedPreviewChanged'):
                playback.detachedPreviewChanged.connect(self._detached_changed)

    liveCandidates = Property('QVariantList', lambda s: list(s._live), notify=changed)
    failure = Property('QVariantMap', lambda s: dict(s._failure), notify=changed)
    recoveryRounds = Property(int, lambda s: s._recovery_rounds, notify=changed)
    taskGeneration = Property(int, lambda s: s._generation, notify=changed)

    state = Property(str, lambda s: s._state, notify=changed)
    busy = Property(bool, lambda s: s._thread is not None, notify=changed)
    query = Property(str, lambda s: s._query, notify=changed)
    candidate = Property('QVariantMap', lambda s: s._candidate.public() if s._candidate else {}, notify=changed)
    draft = Property('QVariantMap', lambda s: s._draft.public() if s._draft else {}, notify=changed)
    status = Property(str, lambda s: s._status, notify=changed)
    error = Property(str, lambda s: s._error, notify=changed)
    elapsed = Property(int, lambda s: s._elapsed, notify=changed)
    playing = Property(bool, lambda s: bool(s._preview_owned and not s._starting and s._playback.playing), notify=changed)
    previewStarting = Property(bool, lambda s: s._starting, notify=changed)
    modelId = Property(str, lambda s: getattr(s._service, 'model_id', s._model), notify=changed)

    @Slot(str, result=bool)
    def openSource(self, url):
        from PySide6.QtGui import QDesktopServices
        safe = public_url(url)
        if not safe:
            return False
        return QDesktopServices.openUrl(QUrl(safe))

    @Slot()
    def open(self):
        if not self._closed:
            self.openRequested.emit()

    def _tick(self):
        self._elapsed = int(monotonic() - self._started)
        self.changed.emit()

    @Slot(str)
    def supplementClue(self, clue):
        clue = public_text(clue, 1000).strip()
        if clue:
            self._start_search((self._query + '；用户补充：' + clue)[:2000], self._seconds, continuation=True)

    @Slot(str, float)
    def findSong(self, query, seconds=15):
        self._start_search(query, seconds)

    def _release_managed_inspection(self):
        # Do not remove previews while a worker may still read their bytes.
        if self._thread is None and self._service is not None:
            invalidate = getattr(self._service, 'invalidate_candidate', None)
            if callable(invalidate):
                invalidate()

    def _trusted_same_version(self, replacement):
        from .source_inspection import SourceInspection
        if self._approval is None or self._approval[0] != self._request_epoch:
            return False
        approved = self._approval[1]
        if (not isinstance(approved.inspection, SourceInspection)
                or not isinstance(replacement.inspection, SourceInspection)
                or approved.query != replacement.query
                or approved.duration_seconds != replacement.duration_seconds):
            return False
        old, new = approved.proposal, replacement.proposal
        # Do not infer song/arrangement from model labels alone. All identity
        # fields must remain exactly equal, IN ADDITION to private image proof.
        if ((old.title, old.artist, old.edition, old.section, old.start_seconds)
                != (new.title, new.artist, new.edition, new.section, new.start_seconds)):
            return False
        if (approved.inspection.source_url != old.selected_url
                or approved.inspection.source_sha256 != old.selected_sha256
                or replacement.inspection.source_url != new.selected_url
                or replacement.inspection.source_sha256 != new.selected_sha256):
            return False
        return approved.inspection.same_version_evidence(replacement.inspection)

    def _start_search(self, query, seconds, *, continuation=False):
        if self._closed:
            return
        self.stopAudition()
        self._release_managed_inspection()
        self._generation += 1
        self._request_epoch += 1
        self._approval = None
        self._cancel.set()
        self._candidate = self._draft = None
        self._query, self._error = str(query).strip()[:2001], ''
        try:
            seconds = duration(seconds)
            if not 1 <= len(self._query) <= 2000:
                raise LabFailure('query', '请输入 1–2000 字的歌曲、歌手或版本线索。')
        except LabFailure as exc:
            self._pending = None
            self._state, self._error, self._status = 'error', exc.message, '调整线索后重新查找。'
            self.changed.emit()
            return
        self._seconds = seconds
        if not continuation:
            # A fresh user request owns a fresh budget and all exclusion identities.
            # Corrections/supplements explicitly continue the existing request below.
            self._recovery_rounds, self._failures = 0, []
            self._rejected, self._rejected_hashes, self._checked_sources = [], [], []
            self._live, self._failure = [], {}
        self._active_candidate = None
        context = (dict(failures=list(self._failures), checked_sources=list(self._checked_sources),
                        round=self._recovery_rounds) if continuation else None)
        self._pending = ('searching', (self._query, seconds, tuple(self._rejected),
                                      tuple(self._rejected_hashes), context))

        if self._thread is None:
            self._launch_pending()
        else:
            self._state, self._status = 'cancelling', '正在结束上次任务，随后查找新线索…'
            self.changed.emit()

    def _launch_pending(self):
        state, args = self._pending
        self._pending = None
        generation, cancelled = self._generation, Event()
        self._cancel = cancelled
        self._state, self._error = state, ''
        self._status = ('AI 助手正在依据失败原因寻找新来源…' if state == 'recovering' else
                        '正在独立查找并检查谱源…' if state == 'searching' else '正在重新获取已确认谱源…')
        self._started, self._elapsed = monotonic(), 0
        self._timer.start()
        def post(kind, value):
            if not self._closed:
                self._event.emit(generation, kind, value)

        def run():
            try:
                if self._service is None:
                    self._service = ScoreLabService(self._home, self._model)
                kw = dict(cancelled=cancelled.is_set,
                          progress=lambda row: post('progress', row))
                if state == 'recovering':
                    result = self._service.discover(self._query, self._seconds, rejected_urls=tuple(self._rejected),
                        rejected_hashes=tuple(self._rejected_hashes), recovery_context=dict(failures=list(self._failures),
                        checked_sources=list(self._checked_sources), round=self._recovery_rounds), **kw)
                elif state == 'searching':
                    result = self._service.discover(args[0], args[1], rejected_urls=args[2],
                        rejected_hashes=args[3], recovery_context=args[4], **kw)
                else:
                    result = self._service.prepare(args, **kw)
                post(state, result)
            except LabFailure as exc:
                post('error', exc.public())
            except Exception:
                post('error', LabFailure('internal', '本次任务未完成，请检查连接或更换谱源后重新查找。').public())
            finally:
                post('done', None)
        self._thread = Thread(target=run, name='score-lab-worker', daemon=False)
        self._thread.start()
        self.changed.emit()

    @Slot(int, str, object)
    def _receive(self, generation, kind, result):
        if self._closed:
            return
        if kind == 'done':
            self._thread.join()
            self._thread = None
            self._tick()
            self._timer.stop()
            if self._state == 'cancelling':
                self._state, self._status = 'idle', '已取消；可重新查找。'
                if not self._pending:
                    self._release_managed_inspection()
            if self._pending:
                self._launch_pending()
            self.changed.emit()
            return
        if generation != self._generation:
            return
        if kind == 'progress':
            # Only public, bounded progress messages cross the Qt boundary.
            self._status = public_text(result.get('message', '正在处理…'))
            observation = result.get('_inspection')
            if isinstance(observation, dict):
                requested, final = observation.get('requestedUrl', ''), observation.get('sourceUrl', '')
                digest = str(observation.get('sourceSha256', ''))
                rejected = observation.get('outcome') == 'rejected'
                # Exact URLs remain private identity keys, separate from display links.
                # At most 32 calls x 3 rounds produce 96 observations / 192 aliases.
                if rejected:
                    self._rejected = list(dict.fromkeys([*self._rejected, *filter(None, (requested, final))]))
                    if len(digest) == 64:
                        self._rejected_hashes = list(dict.fromkeys([*self._rejected_hashes, digest]))
                checked = {k: public_text(observation.get(k, ''), 400 if k == 'message' else 160)
                           for k in ('status', 'outcome', 'title', 'message')}
                checked.update(requestedUrl=public_url(requested), sourceUrl=public_url(final), sourceSha256=digest[:64])
                self._checked_sources = [*self._checked_sources, checked][-96:]
            if result.get('url') and public_url(result['url']):
                row = {k: public_text(result.get(k, ''), 160 if k in ('title','artist','edition') else 400)
                       for k in ('title','artist','edition','message','stage')}
                row['url'] = public_url(result['url'])
                self._live = [r for r in self._live if r['url'] != row['url']][-11:] + [row]
        elif kind == 'error':
            self._candidate = self._draft = None
            self._failure = result if isinstance(result, dict) else dict(code='internal',message=public_text(result))
            self._state, self._error, self._status = 'error', self._failure['message'], '可调整线索，重新查找并确认。'
            recoverable = self._failure.get('code') in {'unreadable','source_unreadable','source_changed','music_contract','format','no_source'}
            if self._active_candidate is not None and recoverable:
                failed = self._active_candidate.proposal
                self._live = [r for r in self._live if r['url'] != public_url(failed.selected_url)][-11:] + [dict(
                    stage='rejected',url=public_url(failed.selected_url),title=public_text(failed.title,160),
                    artist=public_text(failed.artist,160),edition=public_text(failed.edition,160),message=self._error)]
                self._rejected = list(dict.fromkeys([*self._rejected, failed.selected_url]))
                self._rejected_hashes = list(dict.fromkeys([*self._rejected_hashes, failed.selected_sha256]))
            self._failures = [*self._failures, self._failure][-3:]
            if recoverable and self._recovery_rounds < 2:
                if self._assistant is not None and self._assistant.isBusy:
                    self._status = 'AI 助手正在处理其他任务；请等待完成后重新查找。'
                else:
                    self._recovery_rounds += 1
                    self._pending = ('recovering', None)
                    self._state, self._status = 'recovering', 'AI 助手已接手：根据实际失败原因重新检索不同谱源。'
            elif recoverable:
                self._status = '已达到两轮自动恢复上限；请补充歌手、版本或清晰谱源。'
        elif kind in ('searching', 'recovering'):
            self._seconds = result.duration_seconds
            if kind == 'recovering' and self._trusted_same_version(result):
                # Queue after discovery's worker joins. No second user click
                # is necessary ONLY for independently proven same-version art.
                self._active_candidate = result
                self._candidate = None
                self._generation += 1
                self._pending = ('preparing', result)
                self._state, self._status = (
                    'preparing', '已证明是你确认版本的高清谱变体；正在自动准备独立试听草稿…')
            else:
                self._approval = None
                self._candidate = result
                self._state, self._status = (
                    'awaiting_confirmation', '请核对谱源、版本与段落；确认后才开始转谱。')
        elif kind == 'preparing':
            self._draft = result
            self._state, self._status = 'ready', '独立草稿已就绪；请对照谱面试听。'
        self.changed.emit()

    @Slot()
    def confirmSource(self):
        if self._closed or self.busy or self._state != 'awaiting_confirmation' or self._candidate is None:
            return
        candidate = self._candidate
        self._approval = (self._request_epoch, candidate)
        self._active_candidate = candidate
        self._candidate = None  # This exact single-use object never comes back from QML.
        self._generation += 1
        self._pending = ('preparing', candidate)
        self._launch_pending()

    @Slot(str)
    def rejectSource(self, correction):
        if self._candidate:
            proposal = self._candidate.proposal
            self._rejected = list(dict.fromkeys([*self._rejected, proposal.selected_url]))
            self._rejected_hashes = list(dict.fromkeys([*self._rejected_hashes, proposal.selected_sha256]))
        previous = self._query
        seconds = self._candidate.duration_seconds if self._candidate else 15
        if correction.strip():
            self._start_search((previous + '；纠正：' + correction.strip())[:2000], seconds, continuation=True)
        else:
            self.cancel()
            self._status = '已排除此谱源，请补充歌手、版本或段落后重新查找。'
            self.changed.emit()

    @Slot()
    def cancel(self):
        self._generation += 1
        self._cancel.set()
        self._pending = None
        self._candidate = self._draft = None
        self._approval = None
        self.stopAudition()
        self._release_managed_inspection()
        self._state = 'cancelling' if self.busy else 'idle'
        self._status = '正在停止本次任务，请等待连接结束…' if self.busy else '已取消；可重新查找。'
        self._error = ''
        self._live, self._failure = [], {}
        self._active_candidate = None
        self.changed.emit()

    @Slot()
    def audition(self):
        if self._draft is None or self.busy or self._closed or self._playback is None:
            return
        if self._playback.playing or getattr(self._playback, 'preparing', False):
            self._error = '请先停止当前播放，再试听独立草稿。'
            self.changed.emit()
            return
        self._preview_owned = self._starting = True
        self._preview_snapshot = [dict(pitch=n.pitch, startTime=n.start_time, duration=n.duration)
                                  for n in self._draft.state.playback_notes]
        self._error, self._status = '', '正在准备试听…'
        self._preview_timeout.start()
        try:
            self._playback.play_detached_notes(self._draft.state.playback_notes)
            self._playing_changed()
        except Exception:
            # play_detached_notes can clear its snapshot before re-raising.
            self._preview_failed(force=True)
        self.changed.emit()

    def _detached_changed(self):
        if self._preview_owned and self._playback.detachedPreviewNotes != self._preview_snapshot:
            was_starting = self._starting
            self._preview_owned = self._starting = False
            self._preview_timeout.stop()
            # Playback clears detached notes before its terminal playing signal.
            # Revoke stop authority now, and report completion here as well.
            self._status = ('试听已停止；可再次试听或保存独立工程。' if was_starting
                            else '试听已结束；可再次试听或保存独立工程。')
            self.changed.emit()

    def _playing_changed(self):
        if not self._preview_owned:
            return
        if self._playback.playing:
            self._starting = False
            self._preview_timeout.stop()
            self._status = '正在试听独立草稿。'
        elif not self._starting:
            self._preview_owned = False
            self._status = '试听已结束；可再次试听或保存独立工程。'
        self.changed.emit()

    def _preview_failed(self, *_, force=False):
        if self._preview_owned or force:
            self.stopAudition()
            self._status = '试听未能启动；可检查设置后重新试听。'
            self._error = '试听未能启动，请检查音频设置后重试。'
            self.changed.emit()

    @Slot()
    def stopAudition(self):
        owned = self._preview_owned
        self._preview_owned = self._starting = False
        self._preview_timeout.stop()
        if owned and self._playback is not None:
            self._status = '试听已停止；可再次试听或保存独立工程。'
            self._playback.stop_detached_preview()
        self.changed.emit()

    @Slot(QUrl, result=bool)
    def exportProject(self, url):
        if self._draft is None or self._closed or not url.isLocalFile():
            return False
        path = Path(url.toLocalFile())
        if path.suffix.lower() != '.json':
            path = path.with_suffix('.json')
        try:
            save_project_document(path, ProjectDocument(self._draft.state))
        except (OSError, ValueError):
            self._error = '保存失败，请选择可写入的文件位置。'
            self.changed.emit()
            return False
        self._status, self._error = '独立工程已保存，当前作品保持原样。', ''
        self.changed.emit()
        return True

    @Slot()
    def shutdown(self):
        if self._closed:
            return
        self.cancel()
        self._closed = True
        self._timer.stop()
        if self._thread:
            # Cooperative cancellation normally exits immediately. A socket read may
            # still unwind after the UI wait; the non-daemon worker remains owned
            # by Python's shutdown and cannot outlive the application process.
            self._thread.join(timeout=5)
