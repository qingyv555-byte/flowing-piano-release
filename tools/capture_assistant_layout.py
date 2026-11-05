"""Real Qt layout capture, isolated profile and memory-only audio; no online calls."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--platform', default='offscreen', choices=['offscreen', 'windows'])
parser.add_argument('--width', type=int, default=1622)
parser.add_argument('--height', type=int, default=826)
parser.add_argument('--sidebar', type=int, default=440)
parser.add_argument('--source-root', type=Path, default=ROOT)
parser.add_argument('--scenarios', action='store_true')
parser.add_argument('--detached', action='store_true')
parser.add_argument('--hold', type=int, default=0)
args = parser.parse_args()
args.output = args.output.resolve()
ROOT = args.source_root.resolve()
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from tools.studio_evidence import install_memory_output
os.environ['QT_QPA_PLATFORM'] = args.platform
from PySide6.QtCore import QObject, QTimer
from PySide6.QtQml import QQmlExpression, QQmlEngine
from PySide6.QtGui import QWindow
from PySide6.QtTest import QTest
from ai_piano_studio.studio import build_studio_engine
from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.music_agent.assistant_controller import AssistantMessage, AssistantMessageKind
from ai_piano_studio.music_agent.song_workflow import prepare_project

blocked = install_memory_output()
args.output.mkdir(parents=True, exist_ok=True)

def qml(item, code):
    expression = QQmlExpression(QQmlEngine.contextForObject(item), item, code)
    value = expression.evaluate()
    if expression.hasError():
        raise RuntimeError(expression.error().toString())
    return value[0] if isinstance(value, tuple) else value

with tempfile.TemporaryDirectory(prefix='flowing-assistant-layout-') as profile:
    app, engine, editor, playback, keyboard = build_studio_engine(Path(profile))
    root = engine.rootObjects()[0]
    root.setTitle('Flowing Piano · Layout QA')
    root.resize(args.width, args.height)
    root.show()
    initialize_deferred_services(engine)
    appearance = engine.rootContext().contextProperty('studioAppearance')
    appearance.setValue('themeId', 'paper')
    root.setProperty('pageState', 'editor')
    qml(root, 'openAssistantWindow(); true')
    QTest.qWait(300)
    host = root.findChild(QObject, 'studioAssistantHost')
    if root.property('studioAssistantWidth') is not None:
        root.setProperty('studioAssistantWidth', args.sidebar)
    else:
        qml(host, f'Layout.minimumWidth={args.sidebar}; Layout.maximumWidth={args.sidebar}; Layout.preferredWidth={args.sidebar}; true')
    assistant = engine.rootContext().contextProperty('assistant')
    from ai_piano_studio.music_agent.provider_settings import ConnectionResult, ConnectionResultCode
    assistant.selectProvider('codex')
    assistant._on_codex_detection_finished(dict(installed=True, supported=True, login='chatgpt', version='fixture', message='已连接'))
    assistant._provider_settings.set_dynamic_models('codex', ['gpt-6.1-sol', 'a-very-long-model-name-for-layout-verification-2026'])
    assistant.selectModel('gpt-6.1-sol')
    assistant._connection_result = ConnectionResult(ConnectionResultCode.SUCCESS, '连接成功，可以开始使用。')
    assistant._connection_tested = True
    assistant.changed.emit()
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '我想写一段有月光氛围的钢琴曲，20秒。'))
    assistant.messages.append(AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, '可以从安静的分解和弦开始，旋律留一些呼吸。你想更温柔，还是更有力量？'))
    assistant.messages.append(AssistantMessage(AssistantMessageKind.ERROR, '之前的找歌没有找到可验证曲谱。工程未修改。\n可以补充歌手或导入MIDI；未冒充原曲。'))
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '那就写一段温柔的原创，先试听。'))
    workflow = assistant._creation_workflow
    assistant._generation = 1
    assistant._active_generation = 1
    assistant._task_message_start_row = 3
    workflow.generation = 1
    workflow.message_start_row = 3
    workflow.baseline = (editor.projectSessionId, editor.projectRevision)
    assistant._task_progress.start(1, '温柔月光 · 原创钢琴', 'fixture', 'fixture-model')
    state = ProjectState(tuple(Note.create(pitch=60 + i % 6, start_time=i * .36, duration=.3) for i in range(55)))
    song = prepare_project(state, '温柔月光', {'kind': 'ai_original'}, Path(profile))
    workflow._offer_review(song)
    QTest.qWait(500)
    workspace = root.findChild(QObject, 'assistantWorkspace')
    target = root
    if args.detached:
        root.setProperty('studioAssistantDetached', True)
        QTest.qWait(250)
        target = root.findChild(QWindow, 'assistantWindow')
        assert target is not None and target.isVisible()
        target.resize(1000, 720)
        workspace = target.findChild(QObject, 'assistantWorkspace')
    qml(workspace, 'scrollToBottom(); true')
    QTest.qWait(300)
    assert target.grabWindow().save(str(args.output / 'review.png'))
    listing = target.findChild(QObject, 'assistantMessageList')
    composer = target.findChild(QObject, 'assistantComposerPanel')
    metadata = dict(commit='working-tree', platform=args.platform, dpr=root.devicePixelRatio(),
                    window=[root.width(), root.height()], sidebar=host.width(),
                    chatHeight=listing.height(), composerHeight=composer.height(),
                    physicalCapture=[target.grabWindow().width(), target.grabWindow().height()],
                    targetWindow=[target.width(), target.height()], detached=args.detached,
                    sampleData='deterministic offline fixture; real production QML', blockedAudio=blocked)
    (args.output / 'metrics.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    if args.scenarios:
        def capture(name):
            QTest.qWait(180)
            qml(workspace, 'scrollToBottom(); true')
            QTest.qWait(80)
            assert target.grabWindow().save(str(args.output / (name + '.png')))
        # Actual application of an isolated fixture; never touches user work.
        workflow.previewed = True
        assert assistant.applyTaskProject()
        capture('applied')
        (args.output / 'applied-receipts.json').write_text(json.dumps(dict(
            controller=assistant.taskDisplayReceipts,
            workspace=qml(workspace, 'JSON.stringify(taskHistory)'),
            task=assistant.taskProgress, row=assistant.taskMessageRow), indent=2), encoding='utf-8')
        for i in range(30):
            assistant.messages.append(AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE,
                f'{i} · 旋律可以这样呼吸。\n' + '保留主题，在这里让伴奏更轻一些。\n' * (i % 5 + 1)))
        qml(workspace, 'scrollToBottom(); true')
        capture('long-chat')
        assistant._task_message_start_row = len(assistant.messages.entries)
        assistant._generation = 2
        assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '请写一个长标题的原创钢琴主题，用更温柔的伴奏。'))
        assistant._task_progress.start(2, '长标题的原创钢琴主题与带呼吸的分解和弦伴奏', 'fixture', 'fixture')
        from ai_piano_studio.music_agent.progress import ProviderProgressEvent
        assistant._task_progress.record(2, ProviderProgressEvent('generating'))
        assistant._task_progress._started -= 96
        assistant._task_progress.tick()
        assistant._assistant_status = 'thinking'; assistant.changed.emit()
        capture('generating')
        assistant._task_progress.finish(2, 'cancelled')
        assistant.messages.append(AssistantMessage(AssistantMessageKind.SYSTEM_NOTICE, '已取消，工程未修改。'))
        assistant._assistant_status = 'idle'; assistant.changed.emit()
        capture('cancelled')
        assistant._generation = 3
        assistant._task_message_start_row = len(assistant.messages.entries)
        assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '连接与超时检查'))
        assistant._task_progress.start(3, '连接与超时检查', 'fixture', 'fixture')
        assistant._task_progress.finish(3, 'failed', error_code='timeout')
        assistant.messages.append(AssistantMessage(AssistantMessageKind.ERROR, '模型连接超时。请检查网络，再重新尝试。'))
        assistant._assistant_status = 'error'
        assistant._connection_result = ConnectionResult(ConnectionResultCode.UNKNOWN_ERROR, '模型连接失败，请检查本地服务或网络后重试。')
        assistant.changed.emit()
        capture('failed')
        assistant._connection_testing = True; assistant.changed.emit()
        capture('connecting')
        assistant._connection_testing = False
        qml(workspace, 'openConnectionSettings(); true')
        capture('services')
        service = target.findChild(QObject, 'assistantAIServiceDialog')
        qml(service, 'requestClose(); true')
        assistant.selectModel('a-very-long-model-name-for-layout-verification-2026')
        capture('long-model')
        qml(workspace, 'openScopeMenu(activeMessageInput); true')
        capture('scope-menu')
        target.findChild(QObject, 'assistantScopeMenu').close()
        target.findChild(QObject, 'assistantCreationScopeDialog').open()
        capture('scope-dialog')
        target.findChild(QObject, 'assistantCreationScopeDialog').close()
        assistant.clearConversation()
        capture('empty')
        workspace.property('activeMessageInput').setProperty('text', '多行的音乐想法\n' * 15)
        capture('multiline')
    if args.hold:
        QTimer.singleShot(args.hold * 1000, app.quit)
        app.exec()
    workflow.cancel()
    playback.stop()
    if target is not root:
        target.close()
    root.close()
    engine.deleteLater()
    app.processEvents()
