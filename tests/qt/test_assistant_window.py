from __future__ import annotations

from pathlib import Path
import pytest

from PySide6.QtCore import (
    Q_ARG,
    QCoreApplication,
    QMetaObject,
    QObject,
    QPoint,
    QPointF,
    QRect,
    Qt,
    QSettings,
)
from PySide6.QtGui import QGuiApplication, QWheelEvent, QWindow
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from ai_piano_studio.__main__ import build_engine
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
import ai_piano_studio.music_agent.assistant_controller as assistant_controller_module
from ai_piano_studio.music_agent.assistant_controller import (
    AssistantMessage,
    AssistantMessageKind,
    MusicAssistantController,
)
from ai_piano_studio.music_agent.capabilities import ModelCapabilities
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
from ai_piano_studio.music_agent.providers import ProviderError, ProviderEvent, ProviderToolCall
from ai_piano_studio.music_agent.progress import ProviderProgressEvent
from ai_piano_studio.music_agent.tool_contracts import BarRange, MusicPermission


def assistant_qml_source() -> str:
    # The native wrapper and shared workspace retain the same UI contract.
    folder = Path(__file__).parents[2] / "src" / "ai_piano_studio" / "qml"
    return "\n".join((folder / name).read_text(encoding="utf-8")
                     for name in ("AssistantWindow.qml", "AssistantWorkspace.qml", "AssistantComposer.qml"))


def _assistant_window(root: QObject) -> QWindow:
    window = root.findChild(QWindow, "assistantWindow")
    assert window is not None
    return window


def _visual_item(root: QWindow, object_name: str) -> QQuickItem | None:
    def visit(item: QQuickItem) -> QQuickItem | None:
        if item.objectName() == object_name:
            return item
        for child in item.childItems():
            found = visit(child)
            if found is not None:
                return found
        return None

    return visit(root.contentItem())


def _visual_items(root: QWindow, object_name: str) -> list[QQuickItem]:
    matches: list[QQuickItem] = []

    def visit(item: QQuickItem) -> None:
        if item.objectName() == object_name:
            matches.append(item)
        for child in item.childItems():
            visit(child)

    visit(root.contentItem())
    return matches


def test_assistant_window_is_singleton_and_reopens_without_closing_main(qapp) -> None:
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")

    assert assistant is not None
    assistant_window = _assistant_window(root)
    assert assistant_window.isVisible() is False

    assistant.openWindow()
    qapp.processEvents()
    assert assistant_window.isVisible() is True

    assistant_window.close()
    qapp.processEvents()
    assert assistant_window.isVisible() is False
    assert root.isVisible() is True

    assistant.openWindow()
    qapp.processEvents()
    assert assistant_window.isVisible() is True
    assert len(root.findChildren(QWindow, "assistantWindow")) == 1

    root.close()


def test_assistant_window_has_no_transient_parent(qapp) -> None:
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant_window = _assistant_window(root)

    assistant.openWindow()
    qapp.processEvents()

    assert assistant_window.transientParent() is None

    root.close()


def test_assistant_window_uses_the_declared_default_and_minimum_geometry(qapp) -> None:
    """Break caught: the assistant opens too small for its full workflow."""
    source = assistant_qml_source()

    assert "width: 1080" in source
    assert "height: 900" in source
    assert "minimumWidth: 860" in source
    assert "minimumHeight: 640" in source


def test_assistant_window_keeps_conversation_and_composer_in_distinct_layout_regions(qapp) -> None:
    """Break caught: the composer was laid out at the window origin over the chat."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant_window = _assistant_window(root)

    assistant.openWindow()
    qapp.processEvents()

    conversation = _visual_item(assistant_window, "assistantConversationPane")
    message_list = _visual_item(assistant_window, "assistantMessageList")
    composer = _visual_item(assistant_window, "assistantComposerPanel")
    mode_strip = _visual_item(assistant_window, "studioAssistantModeButton")
    input_box = _visual_item(assistant_window, "assistantInput")

    assert conversation is not None
    assert message_list is not None
    assert composer is not None
    assert mode_strip is not None
    assert input_box is not None
    assert conversation.width() > 500
    assert conversation.height() > 250
    assert message_list.width() > 500
    assert message_list.height() > 100
    assert 360 <= composer.width() <= 410
    assert 86 <= composer.height() <= 100
    assert mode_strip.y() >= 0
    assert input_box.width() > 300
    assert composer.y() > conversation.y()

    root.close()


def test_assistant_window_reopens_the_saved_normal_geometry(qapp, monkeypatch, tmp_path: Path) -> None:
    """Break caught: a normal assistant window reopens at its default geometry."""
    settings = QSettings(str(tmp_path / "assistant-window.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(
        MusicAssistantController,
        "_default_settings",
        staticmethod(lambda: settings),
    )
    _app, first_engine, _editor, _playback, _keyboard = build_engine()
    first_root = first_engine.rootObjects()[0]
    first_assistant = first_engine.rootContext().contextProperty("assistant")

    assert QMetaObject.invokeMethod(
        first_assistant,
        "saveWindowGeometry",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", 40),
        Q_ARG("QVariant", 40),
        Q_ARG("QVariant", 960),
        Q_ARG("QVariant", 700),
    )
    first_root.close()
    qapp.processEvents()

    _app, second_engine, _editor, _playback, _keyboard = build_engine()
    second_root = second_engine.rootObjects()[0]
    second_assistant = second_engine.rootContext().contextProperty("assistant")
    second_assistant.openWindow()
    qapp.processEvents()
    reopened = _assistant_window(second_root)

    available = QGuiApplication.primaryScreen().availableGeometry()
    assert reopened.x() >= available.x()
    assert reopened.x() + reopened.width() <= available.x() + available.width() + 2
    assert reopened.y() == 40
    assert reopened.height() == 700
    assert reopened.width() >= reopened.minimumWidth()
    assert reopened.height() >= reopened.minimumHeight()

    second_root.close()


def test_assistant_window_falls_back_to_a_small_primary_screen_for_an_offscreen_geometry(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    """Break caught: an offscreen assistant restore exceeds a smaller primary display."""
    class PrimaryScreen:
        @staticmethod
        def availableGeometry() -> QRect:
            return QRect(100, 200, 640, 480)

    class ScreenApplication:
        @staticmethod
        def primaryScreen() -> PrimaryScreen:
            return PrimaryScreen()

        @staticmethod
        def screens() -> tuple[PrimaryScreen, ...]:
            return (PrimaryScreen(),)

    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    settings = QSettings(str(tmp_path / "assistant-window.ini"), QSettings.Format.IniFormat)
    assistant._window_settings = settings
    settings.setValue("assistantWindow/normalX", 3000)
    settings.setValue("assistantWindow/normalY", 3000)
    settings.setValue("assistantWindow/normalWidth", 900)
    settings.setValue("assistantWindow/normalHeight", 700)
    monkeypatch.setattr(assistant_controller_module, "QGuiApplication", ScreenApplication)

    geometry = assistant.restoreWindowGeometry()

    assert geometry == {
        "x": 100,
        "y": 200,
        "width": 640,
        "height": 480,
        "minimumWidth": 640,
        "minimumHeight": 480,
    }
    root.close()


def test_assistant_window_clamps_a_partially_offscreen_saved_geometry(qapp, monkeypatch, tmp_path: Path) -> None:
    """Break caught: a restored window intersects the screen but still hides its right-side controls."""

    class PrimaryScreen:
        @staticmethod
        def availableGeometry() -> QRect:
            return QRect(0, 0, 1440, 900)

    class ScreenApplication:
        @staticmethod
        def primaryScreen() -> PrimaryScreen:
            return PrimaryScreen()

        @staticmethod
        def screens() -> tuple[PrimaryScreen, ...]:
            return (PrimaryScreen(),)

    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    settings = QSettings(str(tmp_path / "assistant-window.ini"), QSettings.Format.IniFormat)
    assistant._window_settings = settings
    settings.setValue("assistantWindow/normalX", 460)
    settings.setValue("assistantWindow/normalY", 40)
    settings.setValue("assistantWindow/normalWidth", 1080)
    settings.setValue("assistantWindow/normalHeight", 820)
    monkeypatch.setattr(assistant_controller_module, "QGuiApplication", ScreenApplication)

    geometry = assistant.restoreWindowGeometry()

    assert geometry == {
        "x": 360,
        "y": 40,
        "width": 1080,
        "height": 820,
        "minimumWidth": 860,
        "minimumHeight": 640,
    }
    root.close()


def test_assistant_window_never_activates_or_raises_the_main_window() -> None:
    """Break caught: assistant reopening steals focus through MainWindow commands."""
    source = assistant_qml_source()

    assert "mainWindow.requestActivate" not in source
    assert "mainWindow.raise" not in source


def test_assistant_surface_has_compact_project_context_and_capability_badge(qapp) -> None:
    """Break caught: technical status banners hide the project currently targeted by AI."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    assistant_window = _assistant_window(root)

    assert _visual_item(assistant_window, "assistantMoreButton") is not None
    assert _visual_item(assistant_window, "studioAssistantConnectionStatus") is not None
    source = assistant_qml_source()
    assert "当前模型可使用音乐工具" not in source

    root.close()


def test_model_capability_status_is_actionable_in_header_and_settings(qapp) -> None:
    """Break caught: an unknown model is labelled chat-only and can only be verified from an unclear dialog."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)

    for name in (
        "studioAssistantConnectionStatus",
        "assistantConnectionProblem",
        "assistantCreationGuidanceAction",
    ):
        assert window.findChild(QObject, name) is not None

    source = assistant_qml_source()
    assert "currentModelStatus" in source
    assert "verifyMusicToolsAsync()" in source
    assert "root.modelStatus.state" in source
    assert "currentCapabilities.canUseMusicTools" not in source
    assert "textFormat: Text.MarkdownText" in source
    assert "root.modelStatus.detail" in source
    assert 'root.assistant.connectionResult.message + " "' in source
    assert '+ root.modelStatus.detail' in source
    assert "root.proposalConcreteDiff(" in source
    assert "model: [8, 13, 10" not in source

    root.close()


def test_assistant_window_accepts_supplements_during_validation_repair(qapp, qtbot) -> None:
    """Repair stays busy while supplementary input and a separate stop remain available."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)

    assistant._validation_repair_count = 3
    assistant._generation = 43
    assistant._task_progress.start(43, "修正音乐参数", "fixture", "fixture")
    assistant._task_progress.record(43, ProviderProgressEvent("verifying"))
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, "修正音乐参数"))
    assistant._assistant_status = "repairing"
    assistant.changed.emit()
    qapp.processEvents()

    qtbot.waitUntil(lambda: _visual_item(window, "assistantProgressCard") is not None
                    and _visual_item(window, "assistantProgressCard").isVisible())
    progress = _visual_item(window, "assistantProgressCard")
    progress_status = _visual_item(window, "assistantProgressStatus")
    message_input = window.findChild(QObject, "assistantInput")
    send_button = window.findChild(QObject, "assistantSendButton")
    cancel_button = window.findChild(QObject, "assistantStopButton")

    assert progress is not None and progress.property("visible") is True
    assert progress_status is not None
    assert progress_status.property("text") == assistant.taskProgress["stageLabel"]
    assert "验证" in progress_status.property("text")
    assert assistant.assistantProgressText == "正在修正 3 项音乐参数…"
    assert message_input is not None and message_input.property("enabled") is True
    assert send_button is not None and send_button.property("accessibleName") == "发送补充消息"
    assert cancel_button is not None and cancel_button.property("visible") is True
    assert cancel_button.property("enabled") is True

    root.close()


def test_creation_authorization_and_guidance_fit_the_reported_window(qapp) -> None:
    """Break caught: users cannot see/edit authorization and the verification action is clipped at 1134×684."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)
    window.setWidth(1134)
    window.setHeight(684)
    qapp.processEvents()

    scope_bar = _visual_item(window, "assistantEditCreationScopeButton")
    capability_badge = _visual_item(window, "studioAssistantConnectionStatus")

    assert scope_bar is not None
    assert scope_bar.width() >= 70
    assert window.findChild(QObject, "assistantEditCreationScopeButton") is not None
    assert _visual_item(window, "assistantCreationGuidanceAction") is not None
    assert capability_badge is not None
    assert capability_badge.width() >= 36

    root.close()


def test_reported_window_size_renders_all_creation_controls_inside_the_window(
    qapp, tmp_path: Path
) -> None:
    """Break caught: a control can exist in QML yet render outside the reported 1134×684 window."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)
    window.setWidth(1134)
    window.setHeight(684)
    qapp.processEvents()

    for object_name in (
        "assistantHeaderBar",
        "studioAssistantModelSelector",
        "studioAssistantConnectionStatus",
        "studioAssistantConnectionSettings",
        "assistantEditCreationScopeButton",
        "assistantEditCreationScopeButton",
        "assistantComposerPanel",
    ):
        item = _visual_item(window, object_name)
        assert item is not None
        assert item.x() >= 0
        assert item.y() >= 0
        assert item.x() + item.width() <= window.width()
        assert item.y() + item.height() <= window.height()

    badge = _visual_item(window, "studioAssistantConnectionStatus")
    assert badge is not None
    assert badge.width() >= badge.property("implicitWidth")

    dialog = window.findChild(QObject, "assistantCreationScopeDialog")
    assert dialog is not None
    dialog.setProperty("visible", True)
    qapp.processEvents()
    confirm = _visual_item(window, "assistantCreationScopeConfirmButton")
    assert confirm is not None
    assert confirm.width() >= confirm.property("implicitWidth")

    screenshot = window.grabWindow()
    output = tmp_path / "assistant-1134x684.png"
    assert not screenshot.isNull()
    assert screenshot.save(str(output))

    root.close()


def test_assistant_surface_matches_the_reference_information_hierarchy(qapp):
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow(); qapp.processEvents()
    window = _assistant_window(root)
    for name in ("assistantHeaderBar", "assistantModelBar", "assistantConversationPane",
                 "assistantComposerPanel", "studioAssistantModeButton", "assistantInput",
                 "assistantSendButton", "assistantAttachmentMenuButton", "assistantMoreButton"):
        item = _visual_item(window, name)
        assert item is not None and item.isVisible(), name
    assert _visual_item(window, "assistantHeaderBar").height() <= 36
    assert _visual_item(window, "assistantModelBar").height() <= 36
    source = assistant_qml_source()
    for action in ("assistant.applyProposal()", "assistant.discardProposal()", "assistant.auditionProposal()",
                   "assistant.auditionOriginalProposal()", "proposalChangesDialog.open()"):
        assert action in source
    root.close()


def test_assistant_reference_header_has_title_strip_and_branded_catalog_controls(qapp):
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    engine.rootContext().contextProperty("assistant").openWindow(); qapp.processEvents()
    window = _assistant_window(root)
    assert _visual_item(window, "assistantHeaderBar") is not None
    assert _visual_item(window, "studioAssistantModelSelector") is not None
    folder = Path(__file__).parents[2] / "src/ai_piano_studio/qml"
    assert 'StudioModelComboBox {' in assistant_qml_source()
    assert 'AIBrandIcon {' in (folder / 'StudioModelComboBox.qml').read_text(encoding='utf-8')
    root.close()


def test_assistant_reference_actions_and_messages_support_the_primary_workflow(qapp):
    source = assistant_qml_source()
    message_start = source.index('id: messageText')
    assert "selectByMouse: true" in source[message_start:source.index('}', message_start)]
    for action in ('assistantProposalAuditionButton', 'assistantProposalApplyButton', 'assistantProposalMore',
                   'assistantProposalDiscardButton', 'assistantProposalChangesButton'):
        assert 'objectName: "' + action + '"' in source
    assert 'Layout.maximumWidth: Math.min(messageDelegate.kind' in source
    assert '"USER_MESSAGE" ? Qt.AlignRight : Qt.AlignLeft' in source


def test_assistant_mode_strip_exposes_a_single_obvious_current_mode(qapp):
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow(); qapp.processEvents()
    mode = _visual_item(_assistant_window(root), 'studioAssistantModeButton')
    assert mode.property('text') == '提案'
    assistant.confirmCollaborationMode(); qapp.processEvents()
    assert mode.property('text') == '直接修改'
    root.close()


def test_assistant_message_list_declares_follow_latest_and_new_message_affordance(qapp) -> None:
    """Break caught: added chat messages leave the user stranded above the latest response."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    assistant_window = _assistant_window(root)

    assert _visual_item(assistant_window, "assistantNewMessagesButton") is not None
    source = assistant_qml_source()
    assert "property bool followLatest: true" in source
    assert "function scrollToBottom()" in source
    assert "messageList.positionViewAtEnd()" in source
    assert "followLatest = true" in source
    assert "<= 60" in source
    assert "property bool hasUnreadIncoming: false" in source
    assert "hasUnreadIncoming = true" in source
    assert "visible: !root.followLatest && root.hasUnreadIncoming" in source

    root.close()


def test_assistant_window_open_request_restores_a_minimized_window(qapp) -> None:
    """Break caught: opening the assistant leaves a previously minimized window unreachable."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant_window = _assistant_window(root)

    assistant.openWindow()
    qapp.processEvents()
    assistant_window.showMinimized()
    qapp.processEvents()

    assistant.openWindow()
    qapp.processEvents()

    assert assistant_window.isVisible() is True
    assert assistant_window.visibility() != QWindow.Visibility.Minimized

    root.close()


def test_main_window_close_requests_assistant_cleanup(qapp) -> None:
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant_window = _assistant_window(root)

    assistant.openWindow()
    qapp.processEvents()
    assert assistant_window.isVisible() is True

    root.close()
    qapp.processEvents()

    assert assistant_window.isVisible() is False


def test_assistant_surface_uses_catalog_controls_and_real_proposal_actions(qapp) -> None:
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)

    for name in (
        "assistantMoreButton",
        "studioAssistantModelSelector",
        "studioAssistantConnectionStatus",
        "studioAssistantConnectionSettings",
        "assistantMessageList",
        "studioAssistantModeButton",
        "studioAssistantQuickActions",
        "assistantAttachButton",
        "assistantInput",
        "assistantSendButton",
    ):
        assert window.findChild(QObject, name) is not None
    attach_button = window.findChild(QObject, "assistantAttachButton")
    assert attach_button.property("enabled") is True
    assert attach_button.property("text") == "导入歌曲…"

    context = assistant.gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    assert assistant.gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    ).success
    assistant.presentProposal(context)
    qapp.processEvents()

    proposal_card = _visual_item(window, "assistantProposalCard")
    assert proposal_card is not None
    assert proposal_card.property("visible") is True
    source = assistant_qml_source()
    assert "assistant.applyProposal()" in source
    assert "assistant.discardProposal()" in source
    assert source.count('objectName: "assistantProposalCard"') == 1
    for name in (
        "assistantProposalAuditionButton",
        "assistantProposalChangesButton",
        "assistantProposalApplyButton",
        "assistantProposalDiscardButton",
        "assistantProposalConcreteDiff",
    ):
        assert f'objectName: "{name}"' in source
    assert "before.pitch" in source
    assert "after.pitch" in source
    assert "noteId" in source

    root.close()


def test_proposal_inspector_uses_summary_sections_and_keeps_technical_ids_collapsed(qapp) -> None:
    """Break caught: the change dialog renders a dense raw UUID text block."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    root.setProperty("pageState", "editor")
    qapp.processEvents()
    window = _assistant_window(root)
    context = assistant.gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    assert assistant.gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    ).success
    assistant.presentProposal(context)
    # A real staged workspace is enough to build the dialog; no Provider or
    # live project mutation is involved.
    qapp.processEvents()

    source = assistant_qml_source()
    for name in (
        "assistantProposalOriginalAuditionButton",
        "assistantProposalInspectorHeadline",
        "assistantProposalSummaryGrid",
        "assistantProposalBarList",
        "assistantProposalHarmonyDetailsTitle",
        "assistantProposalHarmonyList",
        "assistantProposalDetailsList",
        "assistantProposalTechnicalDetailsToggle",
    ):
        assert f'objectName: "{name}"' in source
    assert "proposalNoteChangeText" not in source
    assert "change.noteId" not in source
    assert 'accessibleName: "试听修改版"' in source
    assert 'objectName: "assistantProposalOnlyChangesToggle"' in source
    assert "ListView" in source
    timeline_source = (
        Path(__file__).parents[2]
        / "src"
        / "ai_piano_studio"
        / "qml"
        / "Timeline.qml"
    ).read_text(encoding="utf-8")
    assert "proposalOverlayNotes" in timeline_source
    assert "proposalOnlyChanges" in timeline_source
    assert "proposalOverlayUseCanvas" in timeline_source
    assert "proposalChangeOverlayCanvas" in timeline_source
    assert "proposalNoteStartSeconds" in timeline_source
    assert "locateProposalNote" in source

    dialog = window.findChild(QObject, "assistantProposalChangesDialog")
    assert dialog is not None
    dialog.setProperty("visible", True)
    qapp.processEvents()
    assert _visual_item(window, "assistantProposalInspectorHeadline") is not None
    assert _visual_item(window, "assistantProposalSummaryGrid") is not None
    technical = _visual_item(window, "assistantProposalTechnicalDetailsToggle")
    assert technical is not None
    assert technical.property("checked") is False
    details = _visual_item(window, "assistantProposalDetailsList")
    assert details is not None
    assert details.property("visible") is False
    dialog.setProperty("detailsExpanded", True)
    qapp.processEvents()
    assert details.property("visible") is True

    timeline = _visual_item(root, "timeline")
    assert timeline is not None
    assert timeline.property("proposalOnlyChanges") is False
    assistant.setProposalOnlyChanges(True)
    qapp.processEvents()
    assert timeline.property("proposalOnlyChanges") is True
    assert timeline.property("proposalOverlayNoteCount") == 1
    note_id = str(assistant.proposalChanges["inspector"]["details"]["added"][0]["technical"]["noteId"])
    located = []

    def on_located(*args) -> None:
        located.append(args)

    assistant.proposalNoteLocateRequested.connect(on_located)
    assistant.locateProposalNote(note_id)
    assert located
    qapp.processEvents()
    assert timeline.property("proposalFocusedNoteId")
    root.close()


@pytest.mark.parametrize('display_mode', ['original', 'smart'])
def test_pending_proposal_overlay_marks_add_update_delete_and_can_locate_notes(qapp, tmp_path, display_mode) -> None:
    """Break caught: Proposal facts exist, but the Piano Roll gives no visual before/after cue."""
    notes = (
        Note.create(id="overlay-source", pitch=60, start_time=0.0, duration=0.5),
        Note.create(id="overlay-deleted", pitch=67, start_time=0.5, duration=0.5),
        Note.create(id="overlay-unchanged", pitch=72, start_time=1.0, duration=0.5),
    )
    editor = EditorController(ProjectState(notes))
    from ai_piano_studio.experience.settings import ExperienceSettings
    experience = ExperienceSettings(settings=QSettings(str(tmp_path / 'view.ini'), QSettings.Format.IniFormat))
    experience.setCompositeDisplayMode(display_mode)
    _app, engine, _controller, _playback, _keyboard = build_engine(controller=editor, experience_settings=experience)
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    qapp.processEvents()
    assistant = engine.rootContext().contextProperty("assistant")
    context = assistant.gateway.create_execution_context(
        authorized_bars=(BarRange(1, 4),),
        permissions=frozenset(
            {MusicPermission.WRITE_NOTES, MusicPermission.WHOLE_PROJECT_WRITE}
        ),
    )
    assert assistant.gateway.execute(
        "update_notes",
        {
            "updates": [
                {"id": "overlay-source", "pitch": 62, "start_time": 1.5, "duration": 0.75}
            ]
        },
        context,
    ).success
    assert assistant.gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 76, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    ).success
    assert assistant.gateway.execute(
        "delete_notes", {"note_ids": ["overlay-deleted"]}, context
    ).success
    assistant.presentProposal(context)
    qapp.processEvents()

    timeline = _visual_item(root, "timeline")
    assert timeline is not None
    inspector = assistant.proposalChanges["inspector"]
    added_id = str(inspector["details"]["added"][0]["technical"]["noteId"])
    assert timeline.property("proposalOverlayNoteCount") == 4
    assert _visual_item(root, "proposalOverlayNote-added-" + added_id) is not None
    assert _visual_item(root, "proposalOverlayNote-updated-overlay-source") is not None
    assert _visual_item(root, "proposalOverlayNote-ghost-overlay-source") is not None
    assert _visual_item(root, "proposalOverlayNote-deleted-overlay-deleted") is not None

    unchanged = _visual_item(root, "noteItem-overlay-unchanged")
    if display_mode == 'smart':
        group = next(g for g in editor.compositeModel.groups if 'overlay-unchanged' in g.child_note_ids)
        unchanged = _visual_item(root, 'compositeNoteItem-' + group.group_id)
    assert unchanged is not None
    if display_mode == 'original':
        assert unchanged.opacity() > 0.9
    else:
        assert unchanged.opacity() == pytest.approx(0.86)
    assistant.setProposalOnlyChanges(True)
    qapp.processEvents()
    assert unchanged.opacity() < 0.5

    assistant.locateProposalNote("overlay-source")
    qapp.processEvents()
    assert timeline.property("proposalFocusedNoteId") == "overlay-source"
    root.close()


def test_harmony_proposal_renders_a_readable_harmony_change_section(qapp) -> None:
    """Break caught: Harmony changes are counted but never shown to the user."""
    melody = tuple(
        Note.create(
            id=f"harmony-melody-{index}",
            pitch=pitch,
            start_time=start_time,
            duration=duration,
        )
        for index, (pitch, start_time, duration) in enumerate(
            (
                (72, 0.0, 0.5),
                (83, 0.5, 0.25),
                (76, 0.75, 0.25),
                (79, 1.0, 0.5),
                (77, 1.5, 0.25),
                (81, 1.75, 0.125),
                (72, 1.875, 0.125),
            )
        )
    )
    editor = EditorController(ProjectState(melody))
    _app, engine, _editor, _playback, _keyboard = build_engine(controller=editor)
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    qapp.processEvents()
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    context = assistant.gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset(
            {MusicPermission.READ_HARMONY, MusicPermission.WRITE_HARMONY}
        ),
    )
    available = assistant.gateway.execute(
        "get_harmony_context", {"start_bar": 1, "end_bar": 1}, context
    )
    assert available.success
    measure = available.data["measures"][0]
    assert assistant.gateway.execute(
        "apply_chord",
        {
            "measure_index": measure["measure_index"],
            "candidate_index": measure["candidates"][0]["candidate_index"],
            "harmony_context_token": available.data["harmony_context_token"],
        },
        context,
    ).success
    assistant.presentProposal(context)
    qapp.processEvents()

    window = _assistant_window(root)
    dialog = window.findChild(QObject, "assistantProposalChangesDialog")
    assert dialog is not None
    dialog.setProperty("visible", True)
    qapp.processEvents()
    harmony_title = _visual_item(window, "assistantProposalHarmonyDetailsTitle")
    harmony_list = _visual_item(window, "assistantProposalHarmonyList")
    assert harmony_title is not None
    assert harmony_title.property("visible") is True
    assert harmony_list is not None
    assert harmony_list.property("visible") is True
    assert harmony_list.property("count") == 1
    inspector = assistant.proposalChanges["inspector"]
    assert inspector["summary"]["harmonyChanged"] == 1
    assert inspector["harmony"][0]["after"]["chordSymbol"]
    assert "generatedNoteIds" not in inspector["harmony"][0]["after"]
    root.close()


def test_bpm_rescaled_snapshot_does_not_create_a_false_proposal_ghost(qapp) -> None:
    """Break caught: a BPM-only rescale looks like a moved note in the overlay."""
    note = Note.create(
        id="bpm-anchor",
        pitch=60,
        start_time=2.0,
        duration=0.5,
        velocity=80,
    )
    editor = EditorController(ProjectState((note,)))
    _app, engine, _editor, _playback, _keyboard = build_engine(controller=editor)
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    qapp.processEvents()
    assistant = engine.rootContext().contextProperty("assistant")
    context = assistant.gateway.create_execution_context(
        authorized_bars=(),
        permissions=frozenset(
            {
                MusicPermission.WRITE_NOTES,
                MusicPermission.WRITE_BPM,
                MusicPermission.WHOLE_PROJECT_WRITE,
            }
        ),
    )
    assert assistant.gateway.execute(
        "set_note_velocity", {"note_ids": [note.id], "velocity": 96}, context
    ).success
    assert assistant.gateway.execute("set_bpm", {"bpm": 100}, context).success
    assistant.presentProposal(context)
    qapp.processEvents()

    timeline = _visual_item(root, "timeline")
    assert timeline is not None
    assert timeline.property("proposalOverlayNoteCount") == 1
    assert _visual_item(root, "proposalOverlayNote-updated-bpm-anchor") is not None
    assert _visual_item(root, "proposalOverlayNote-ghost-bpm-anchor") is None
    root.close()


def test_large_pending_proposal_uses_a_canvas_overlay_instead_of_one_item_per_note(qapp) -> None:
    """Break caught: a large Proposal creates hundreds of interactive QML delegates."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    qapp.processEvents()
    assistant = engine.rootContext().contextProperty("assistant")
    context = assistant.gateway.create_execution_context(
        authorized_bars=(),
        permissions=frozenset(
            {MusicPermission.WRITE_NOTES, MusicPermission.WHOLE_PROJECT_WRITE}
        ),
    )
    notes = [
        {
            "pitch": 36 + index % 48,
            "start_time": index * 0.25,
            "duration": 0.125,
            "velocity": 80,
        }
        for index in range(121)
    ]
    assert assistant.gateway.execute("add_notes", {"notes": notes}, context).success
    assistant.presentProposal(context)
    qapp.processEvents()

    timeline = _visual_item(root, "timeline")
    assert timeline is not None
    assert timeline.property("proposalOverlayNoteCount") == 121
    assert timeline.property("proposalOverlayUseCanvas") is True
    overlay_canvas = _visual_item(root, "proposalChangeOverlayCanvas")
    assert overlay_canvas is not None
    assert overlay_canvas.property("visible") is True
    first_id = str(
        assistant.proposalChanges["inspector"]["details"]["added"][0]["technical"]["noteId"]
    )
    assert not _visual_items(root, "proposalOverlayNote-added-" + first_id)
    assistant.locateProposalNote(first_id)
    qapp.processEvents()
    assert timeline.property("proposalFocusedNoteId") == first_id
    root.close()


def test_proposal_location_expands_timeline_for_a_note_beyond_live_duration(qapp) -> None:
    """Break caught: locating a pending note outside the live project clamps back to zero."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    qapp.processEvents()
    assistant = engine.rootContext().contextProperty("assistant")
    context = assistant.gateway.create_execution_context(
        authorized_bars=(),
        permissions=frozenset(
            {MusicPermission.WRITE_NOTES, MusicPermission.WHOLE_PROJECT_WRITE}
        ),
    )
    assert assistant.gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 30.0, "duration": 0.5, "velocity": 96}]},
        context,
    ).success
    assistant.presentProposal(context)
    qapp.processEvents()
    detail = assistant.proposalChanges["inspector"]["details"]["added"][0]
    assistant.locateProposalNote(str(detail["technical"]["noteId"]))
    qapp.processEvents()

    timeline = _visual_item(root, "timeline")
    assert timeline is not None
    assert timeline.property("proposalTotalSeconds") > 30.0
    start_seconds = detail["afterNote"]["startBeats"] * timeline.property("secondsPerBeat")
    visible_seconds = (timeline.width() - timeline.property("keyWidth")) / timeline.property(
        "pixelsPerSecond"
    )
    assert timeline.property("viewStartSeconds") > 0.0
    assert timeline.property("viewStartSeconds") <= start_seconds
    assert start_seconds <= timeline.property("viewStartSeconds") + visible_seconds
    root.close()


def test_actionable_advice_shows_a_local_generate_preview_button_below_the_reply(qapp) -> None:
    """Break caught: actionable advice forces the user to retype the same request to get a Proposal."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    assistant._pending_music_intent = "EDIT_MUSIC"
    assistant._pending_music_instruction = "让当前旋律更梦幻，保留主题，先给建议"
    assistant._messages.append(
        AssistantMessage(
            AssistantMessageKind.ASSISTANT_MESSAGE,
            "建议保留主题，并逐步增加高音区与力度层次。",
        )
    )
    assistant._messages.append(
        AssistantMessage(AssistantMessageKind.ACTIVITY, "已读取当前音符。", title="音乐处理")
    )
    assistant.changed.emit()
    qapp.processEvents()
    window = _assistant_window(root)

    assert assistant.canGenerateAdvicePreview is True
    action = _visual_item(window, "assistantAdvicePreviewButton")
    assert action is not None
    assert action.property("visible") is True
    assert action.property("text") == "按这个建议生成可试听方案"
    assert action.width() >= 200

    root.close()


def test_open_assistant_direct_edit_smoke_commits_a_real_piano_roll_change(
    qapp, tmp_path: Path
) -> None:
    """Break caught: the visible Assistant can describe an edit but never commits it."""

    class ScriptedProvider:
        capabilities = ModelCapabilities(True, False, False, False, True)

        def __init__(self) -> None:
            self.scripts = [
                (
                    ProviderEvent(
                        tool_calls=(
                            ProviderToolCall(
                                "read-1",
                                "get_notes",
                                '{"start_bar":1,"end_bar":1}',
                            ),
                        )
                    ),
                ),
                (
                    ProviderEvent(
                        tool_calls=(
                            ProviderToolCall(
                                "write-1",
                                "update_notes",
                                '{"updates":[{"id":"ui-source","pitch":62}]}',
                            ),
                        )
                    ),
                ),
            ]
            self.tool_schemas: list[list[dict[str, object]]] = []

        def stream_events(self, _messages, tools, _reasoning, _attachments):
            self.tool_schemas.append(list(tools))
            if self.scripts:
                yield from self.scripts.pop(0)
            else:
                yield ProviderEvent(finish_reason="stop")

        def get_capabilities(self, _model_id: str) -> ModelCapabilities:
            return self.capabilities

        def normalize_error(self, _error: BaseException) -> ProviderError:
            return ProviderError("scripted provider failed")

        def list_models(self) -> list[str]:
            return ["scripted-model"]

    note = Note.create(id="ui-source", pitch=60, start_time=0.5, duration=0.5)
    editor = EditorController(ProjectState((note,), frozenset({note.id})))
    _app, engine, _editor, _playback, _keyboard = build_engine(controller=editor)
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    provider = ScriptedProvider()
    settings = ProviderSettingsService(
        settings=QSettings(str(tmp_path / "ui-assistant.ini"), QSettings.Format.IniFormat),
        credentials=MemoryCredentialStore(),
        provider_factory=lambda _provider_id: provider,
    )
    settings.save(
        "compatible",
        "Scripted",
        "https://scripted.example.test/v1",
        "scripted-key",
        "scripted-model",
        True,
    )
    settings.record_capability_verification("compatible", "scripted-model", provider.capabilities)
    assistant._provider_settings = settings
    assistant._window_settings = QSettings(
        str(tmp_path / "ui-assistant-window.ini"), QSettings.Format.IniFormat
    )
    assert assistant.selectProvider("compatible") is True
    assert assistant.selectModel("scripted-model") is True
    assistant.confirmCollaborationMode()
    assistant.openWindow()
    qapp.processEvents()

    window = _assistant_window(root)
    assert window.isVisible() is True
    message_input = window.findChild(QObject, "assistantInput")
    send_button = window.findChild(QObject, "assistantSendButton")
    assert message_input is not None and send_button is not None
    prompt = "直接把当前选中的部分改得更梦幻，现在执行。"
    message_input.setProperty("text", prompt)
    qapp.processEvents()
    assert send_button.property("enabled") is True

    assert assistant.sendMessage(prompt) is True
    for _ in range(400):
        qapp.processEvents()
        if assistant.assistantStatus == "idle" and not assistant._workers:
            break
        QTest.qWait(5)

    assert assistant.assistantStatus == "idle"
    assert editor.state.active_notes[0].pitch == 62
    assert editor._history.undo_depth == 1
    assert assistant.lastTurnResult["intent"] == "EDIT_MUSIC"
    assert assistant.lastTurnResult["writeCount"] == 1
    assert len(provider.tool_schemas) == 3
    assert provider.tool_schemas[0] == provider.tool_schemas[1]
    root.close()


def test_open_assistant_suggestion_returns_text_and_visible_proposal_in_one_turn(
    qapp, tmp_path: Path
) -> None:
    """Break caught: suggestion mode shows prose but no local Audition action without a second call."""

    class ScriptedProposalProvider:
        capabilities = ModelCapabilities(True, False, False, False, True)

        def __init__(self) -> None:
            self.scripts = [
                (
                    ProviderEvent(
                        text_delta="我会保留主题，并轻微抬高旋律，让变化更梦幻。",
                        tool_calls=(
                            ProviderToolCall(
                                "read-proposal",
                                "get_notes",
                                '{"start_bar":1,"end_bar":1}',
                            ),
                        ),
                    ),
                ),
                (
                    ProviderEvent(
                        tool_calls=(
                            ProviderToolCall(
                                "write-proposal",
                                "update_notes",
                                '{"updates":[{"id":"ui-proposal-source","pitch":62}]}',
                            ),
                        )
                    ),
                ),
                (ProviderEvent(text_delta="方案已准备好，可先试听。", finish_reason="stop"),),
            ]

        def stream_events(self, _messages, _tools, _reasoning, _attachments):
            yield from self.scripts.pop(0)

        def get_capabilities(self, _model_id: str) -> ModelCapabilities:
            return self.capabilities

        def normalize_error(self, _error: BaseException) -> ProviderError:
            return ProviderError("scripted proposal provider failed")

        def list_models(self) -> list[str]:
            return ["scripted-proposal-model"]

    note = Note.create(
        id="ui-proposal-source", pitch=60, start_time=0.5, duration=0.5
    )
    editor = EditorController(ProjectState((note,), frozenset({note.id})))
    original_revision = editor.projectRevision
    _app, engine, _editor, _playback, _keyboard = build_engine(controller=editor)
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    provider = ScriptedProposalProvider()
    settings = ProviderSettingsService(
        settings=QSettings(
            str(tmp_path / "ui-suggestion.ini"), QSettings.Format.IniFormat
        ),
        credentials=MemoryCredentialStore(),
        provider_factory=lambda _provider_id: provider,
    )
    settings.save(
        "compatible",
        "Scripted",
        "https://scripted.example.test/v1",
        "scripted-key",
        "scripted-proposal-model",
        True,
    )
    settings.record_capability_verification(
        "compatible", "scripted-proposal-model", provider.capabilities
    )
    assistant._provider_settings = settings
    assistant._window_settings = QSettings(
        str(tmp_path / "ui-suggestion-window.ini"), QSettings.Format.IniFormat
    )
    assert assistant.selectProvider("compatible") is True
    assert assistant.selectModel("scripted-proposal-model") is True
    assert assistant.mode == "suggestion"
    assistant.openWindow()
    qapp.processEvents()

    window = _assistant_window(root)
    message_input = window.findChild(QObject, "assistantInput")
    assert message_input is not None
    prompt = "让当前旋律更梦幻，保留主题，先给建议"
    message_input.setProperty("text", prompt)
    assert QMetaObject.invokeMethod(
        window, "sendAssistantMessage", Qt.ConnectionType.DirectConnection
    )
    for _ in range(400):
        qapp.processEvents()
        if assistant.proposal["available"] is True and not assistant._workers:
            break
        QTest.qWait(5)
    # Let the ListView process the deferred content-height and follow-latest
    # callbacks scheduled after the worker publishes the proposal row.
    QTest.qWait(100)
    qapp.processEvents()

    proposal_cards = _visual_items(window, "assistantProposalCard")
    audition_buttons = _visual_items(window, "assistantProposalAuditionButton")
    visible_proposal_cards = [
        item for item in proposal_cards if item.property("visible") is True
    ]
    visible_audition_buttons = [
        item for item in audition_buttons if item.property("visible") is True
    ]
    assert assistant.proposal["available"] is True
    assert visible_proposal_cards, [
        {
            "visible": item.property("visible"),
            "parentVisible": item.parent().property("visible"),
            "delegateKind": item.parent().parent().property("kind"),
            "delegateProposalId": item.parent().parent().property("proposalId"),
            "currentProposalId": assistant.proposal["proposalId"],
            "messageKinds": [entry.kind.value for entry in assistant.messages.entries],
        }
        for item in proposal_cards
    ]
    assert visible_audition_buttons
    assert visible_audition_buttons[0].property("text") == "试听"
    message_kinds = [entry.kind for entry in assistant.messages.entries]
    assert message_kinds.index(AssistantMessageKind.ASSISTANT_MESSAGE) < message_kinds.index(
        AssistantMessageKind.PROPOSAL
    )
    assert editor.state.active_notes[0].pitch == 60
    assert editor.projectRevision == original_revision
    assert editor._history.undo_depth == 0
    root.close()


def test_assistant_send_button_stays_inside_its_composer_field_at_minimum_geometry(qapp) -> None:
    """The send action must keep a reserved slot instead of overflowing the input frame."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)
    window.setWidth(860)
    window.setHeight(640)
    qapp.processEvents()

    composer_field = _visual_item(window, "assistantComposerField")
    send = _visual_item(window, "assistantSendButton")
    assert composer_field is not None
    assert send is not None

    field_top_left = composer_field.mapToItem(
        window.contentItem(), QPointF(0, 0)
    )
    field_bottom_right = composer_field.mapToItem(
        window.contentItem(),
        QPointF(composer_field.width(), composer_field.height()),
    )
    send_top_left = send.mapToItem(window.contentItem(), QPointF(0, 0))
    send_bottom_right = send.mapToItem(
        window.contentItem(), QPointF(send.width(), send.height())
    )
    assert 28 <= send.width() <= 36
    assert send_top_left.x() >= field_top_left.x()
    assert send_top_left.y() >= field_top_left.y()
    assert send_bottom_right.x() <= field_bottom_right.x()
    assert send_bottom_right.y() <= field_bottom_right.y()

    root.close()


def test_assistant_exposes_curated_quick_actions_without_provider_tool_syntax(qapp) -> None:
    """Quick actions are visible, named, and routed through the assistant intent API."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)

    quick_row = _visual_item(window, "studioAssistantQuickActions")
    assert quick_row is not None
    assert 28 <= quick_row.width() <= 40
    source = assistant_qml_source()
    for label in ("续写这段", "优化旋律", "添加和弦", "制造高潮", "更梦幻"):
        assert label in source
    assert "composer.workspace.prefillQuickAction(modelData.id, modelData.prompt)" in source
    assert "assistant.sendQuickAction(root.pendingQuickActionId, root.activeMessageInput.text)" in source

    root.close()


def test_assistant_window_exposes_cancel_only_for_an_active_turn(qapp) -> None:
    """Break caught: an active provider turn has no obvious, state-bound Cancel control."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)
    cancel = window.findChild(QObject, "assistantStopButton")

    assert cancel is not None
    assert cancel.property("visible") is False
    assistant._assistant_status = "thinking"
    assistant.changed.emit()
    qapp.processEvents()
    assert cancel.property("enabled") is True
    assert cancel.property("visible") is True
    assistant._assistant_status = "tool_running"
    assistant.changed.emit()
    qapp.processEvents()
    assert cancel.property("enabled") is True

    root.close()


def test_composer_keeps_send_and_stop_separate_for_an_active_turn(qapp) -> None:
    """A running task must allow typed supplementary input without turning Send into Stop."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    send = _assistant_window(root).findChild(QObject, "assistantSendButton")

    assert send is not None
    assistant._assistant_status = "thinking"
    assistant.changed.emit()
    qapp.processEvents()

    _assistant_window(root).findChild(QObject, "assistantInput").setProperty('text', '再柔和一点')
    qapp.processEvents()
    assert send.property("accessibleName") == "发送补充消息"
    assert send.property("enabled") is True
    stop = _assistant_window(root).findChild(QObject, 'assistantStopButton')
    assert stop.property('visible') and stop.property('enabled')
    root.close()


def test_composer_remains_retryable_after_a_recoverable_error(qapp) -> None:
    """Break caught: an error leaves a typed retry message with Send disabled."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)
    message_input = window.findChild(QObject, "assistantInput")
    send = window.findChild(QObject, "assistantSendButton")

    assert message_input is not None and send is not None
    message_input.setProperty("text", "什么是和弦？")
    assistant._assistant_status = "error"
    assistant.changed.emit()
    qapp.processEvents()
    assert send.property("enabled") is True

    root.close()


def test_assistant_progress_card_explains_active_music_creation(qapp, qtbot) -> None:
    """Break caught: an active generation only changes the send button text."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)
    assistant._generation = 43
    assistant._task_progress.start(43, "创作一段旋律", "fixture", "fixture")
    assistant._task_progress.record(43, ProviderProgressEvent("understanding"))
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, "创作一段旋律"))
    assistant._assistant_status = "thinking"
    assistant.changed.emit()
    qtbot.waitUntil(lambda: _visual_item(window, "assistantProgressCard") is not None
                    and _visual_item(window, "assistantProgressCard").isVisible())
    progress = _visual_item(window, "assistantProgressCard")
    progress_text = _visual_item(window, "assistantProgressStatus")
    assert progress is not None and progress_text is not None
    assert progress.property("visible") is True
    assert "创作" in progress_text.property("text") or "理解" in progress_text.property("text")

    assistant._assistant_status = "idle"
    assistant._task_progress.finish(43, "responded")
    assistant.changed.emit()
    qapp.processEvents()
    # The legacy running-only card disappears; creator receipts are separate.
    assert progress.property("visible") is False
    root.close()


def test_settings_dialog_has_room_for_wrapped_actions(qapp) -> None:
    """Break caught: the settings footer overflows when its actions share one row."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    dialog = _assistant_window(root).findChild(QObject, "assistantSettingsDialog")

    assert dialog is not None
    assert dialog.property("width") >= 700
    source = assistant_qml_source()
    assert 'objectName: "assistantSettingsFooter"' in source
    assert "GridLayout" in source
    root.close()


def test_reasoning_control_hides_when_the_selected_model_does_not_support_it(qapp) -> None:
    """Break caught: an unavailable reasoning control wastes header space as a disabled selector."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)
    window.findChild(QObject, "assistantSettingsDialog").open()
    qapp.processEvents()
    reasoning = window.findChild(QObject, "assistantReasoningSelector")

    assert reasoning is not None
    assert reasoning.property("visible") is False

    assert assistant.selectModel("o3") is True
    qapp.processEvents()

    assert reasoning.property("visible") is True
    assert reasoning.property("displayText") == "思考：关闭"

    root.close()


def test_assistant_mode_controls_visibly_identify_the_active_mode(qapp):
    from tests.qt.test_studio_workflows import click_item, qml
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow(); qapp.processEvents()
    window = _assistant_window(root)
    mode = _visual_item(window, 'studioAssistantModeButton')
    click_item(window, mode); qapp.processEvents()
    suggestion = window.findChild(QObject, 'studioAssistantSuggestionMode')
    collaboration = window.findChild(QObject, 'studioAssistantDirectMode')
    assert suggestion.property('checked') and not collaboration.property('checked')
    click_item(window, collaboration); qapp.processEvents()
    confirm = window.findChild(QObject, 'assistantCollaborationConfirmDialog')
    assert confirm.property('opened')
    assistant.confirmCollaborationMode(); qml(confirm, 'close(); true'); qapp.processEvents()
    click_item(window, mode); qapp.processEvents()
    assert collaboration.property('checked') and not suggestion.property('checked')
    click_item(window, suggestion); qapp.processEvents()
    assert assistant.mode == 'suggestion'
    root.close()


def test_settings_wizard_exposes_safe_key_input_and_third_party_warning(qapp) -> None:
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)

    settings_dialog = window.findChild(QObject, "assistantSettingsDialog")
    collaboration_dialog = window.findChild(QObject, "assistantCollaborationConfirmDialog")
    assert settings_dialog is not None
    assert collaboration_dialog is not None

    for name in (
        "assistantSettingsDialog",
        "assistantBaseUrlField",
        "assistantApiKeyField",
        "assistantSavedApiKeyMask",
        "assistantModelField",
        "assistantActiveModelHint",
        "assistantRefreshModelsButton",
        "assistantConnectionResult",
        "assistantTestConnectionButton",
        "assistantThirdPartyWarning",
    ):
        assert window.findChild(QObject, name) is not None

    assert settings_dialog.findChild(QObject, "assistantRefreshModelsButton") is not None
    assert collaboration_dialog.findChild(QObject, "assistantRefreshModelsButton") is None

    source = assistant_qml_source()
    assert "echoMode: TextInput.Password" in source
    assert 'apiKeyField.text = ""' in source
    assert "assistant.refreshModelCatalogAsync()" in source
    assert "AI Piano Studio 无法保证第三方服务器如何处理这些数据" in source
    assert "setupSteps" in source
    assert 'credentialStatus === "saved"' in source
    assert "已保存：••••••••••••" in source
    assert "当前创作与验证使用：" in source
    assert "留空保留已保存的密钥" in source

    root.close()


def test_settings_expose_provider_specific_api_key_pages(qapp) -> None:
    """Break caught: API-key setup leaves users without a provider-specific key acquisition link."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)
    settings_dialog = window.findChild(QObject, "assistantSettingsDialog")
    key_link = window.findChild(QObject, "assistantApiKeyLinkButton")

    assert settings_dialog is not None
    assert key_link is not None
    settings_dialog.setProperty("visible", True)
    qapp.processEvents()
    selector = window.findChild(QObject, "assistantConnectionProfileSelector")
    assert selector is not None
    # Exercise the settings card path, which refreshes the editable draft.
    labels = {"openai": "OpenAI", "deepseek": "DeepSeek", "anthropic": "Anthropic",
              "gemini": "Gemini", "qwen": "通义千问", "kimi": "Kimi"}
    profiles = assistant.connectionProfiles
    for provider_id, label in labels.items():
        index = next(index for index, profile in enumerate(profiles) if profile["id"] == provider_id)
        assert QMetaObject.invokeMethod(selector, "selectConnection", Q_ARG("QVariant", index), Q_ARG("QVariant", False))
        qapp.processEvents()
        assert assistant.currentProviderId == provider_id
        assert key_link.property("visible") is True
        assert key_link.property("text") == f"获取 {label} API Key"

    root.close()


def test_settings_expose_connection_profiles_and_music_tool_verification(qapp) -> None:
    """Break caught: a custom connection and unknown model have no beginner-facing setup path."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)

    for name in (
        "assistantConnectionProfileSelector",
        "assistantAddConnectionButton",
        "assistantCreationGuidanceAction",
        "assistantDeleteConnectionButton",
    ):
        assert window.findChild(QObject, name) is not None
    root.close()


def test_public_provider_setup_retains_key_until_user_acknowledges_warning(qapp, tmp_path: Path) -> None:
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant._provider_settings = ProviderSettingsService(
        settings=QSettings(str(tmp_path / "assistant.ini"), QSettings.Format.IniFormat),
        credentials=MemoryCredentialStore(),
    )
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)
    dialog = window.findChild(QObject, "assistantSettingsDialog")
    warning = window.findChild(QObject, "assistantThirdPartyWarning")
    api_key_field = window.findChild(QObject, "assistantApiKeyField")
    base_url_field = window.findChild(QObject, "assistantBaseUrlField")
    model_field = window.findChild(QObject, "assistantModelField")
    assert dialog is not None and warning is not None
    assert api_key_field is not None and base_url_field is not None and model_field is not None

    dialog.setProperty("visible", True)
    api_key_field.setProperty("text", "bridge-key")
    base_url_field.setProperty("text", "https://bridge.example.test/v1")
    model_field.setProperty("text", "bridge-model")
    assert QMetaObject.invokeMethod(
        dialog,
        "saveCurrent",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", False),
        Q_ARG("QVariant", False),
    )
    qapp.processEvents()

    assert warning.property("visible") is True
    assert api_key_field.property("text") == "bridge-key"

    assert QMetaObject.invokeMethod(
        dialog,
        "saveCurrent",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", True),
        Q_ARG("QVariant", False),
    )
    qapp.processEvents()

    assert api_key_field.property("text") == ""
    assert assistant.providerSetup("openai")["configured"] is True
    assert dialog.property("connectionDirty") is False
    assert base_url_field.property("text") == assistant.providerSetup("openai")["baseUrl"]
    root.close()


def test_deleting_custom_connection_refreshes_current_fields(qapp, tmp_path: Path) -> None:
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant._provider_settings = ProviderSettingsService(
        settings=QSettings(str(tmp_path / "connections.ini"), QSettings.Format.IniFormat),
        credentials=MemoryCredentialStore(),
    )
    result = assistant.createCustomConnection(
        "Local test", "http://127.0.0.1:9000/v1", "test-only-key", "test-model", True
    )
    assert result["success"]
    assistant.openWindow()
    qapp.processEvents()
    window = _assistant_window(root)
    dialog = window.findChild(QObject, "assistantSettingsDialog")
    dialog.setProperty("visible", True)
    qapp.processEvents()
    field = window.findChild(QObject, "assistantBaseUrlField")
    assert field.property("text") == "http://127.0.0.1:9000/v1"
    button = window.findChild(QObject, "assistantDeleteConnectionButton")
    assert QMetaObject.invokeMethod(button, "clicked", Qt.ConnectionType.DirectConnection)
    qapp.processEvents()
    assert assistant.currentProviderId == "compatible"
    assert field.property("text") == assistant.providerSetup("compatible")["baseUrl"]
    assert dialog.property("connectionDirty") is False
    root.close()


def _item_center_in_window(window: QWindow, item: QQuickItem) -> QPointF:
    point = item.mapToItem(window.contentItem(), QPointF(item.width() / 2, item.height() / 2))
    return QPointF(point.x(), point.y())


def test_assistant_chat_scroll_up_pauses_follow_and_new_message_affordance_restores(
    qapp,
) -> None:
    """Break caught: a wheel scroll away from the latest reply still force-jumps the chat."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    assistant_window = _assistant_window(root)
    message_list = _visual_item(assistant_window, "assistantMessageList")
    new_messages_button = _visual_item(assistant_window, "assistantNewMessagesButton")
    assert message_list is not None and new_messages_button is not None

    for index in range(40):
        assistant.messages.append(
            AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, f"第 {index} 条回复。")
        )
    qapp.processEvents()
    QTest.qWait(150)

    assert assistant_window.property("followLatest") is True
    assert new_messages_button.property("visible") is False

    # Scroll away from the bottom with a real drag gesture. The ListView may bounce
    # back to the bottom on some event-loop schedules, so retry until followLatest
    # is stably paused.
    center = _item_center_in_window(assistant_window, message_list)
    start = center.toPoint()
    for _attempt in range(5):
        QTest.mousePress(
            assistant_window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
        )
        for step in range(1, 6):
            QTest.mouseMove(assistant_window, start + QPoint(0, step * 40), 20)
            qapp.processEvents()
        QTest.mouseRelease(
            assistant_window,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            start + QPoint(0, 200),
        )
        qapp.processEvents()
        QTest.qWait(200)
        if assistant_window.property("followLatest") is False:
            break

    assert assistant_window.property("followLatest") is False

    assistant.messages.append(
        AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, "新到的回复。")
    )
    qapp.processEvents()
    QTest.qWait(50)

    assert assistant_window.property("hasUnreadIncoming") is True
    assert new_messages_button.property("visible") is True

    QTest.mouseClick(
        assistant_window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        _item_center_in_window(assistant_window, new_messages_button).toPoint(),
    )
    qapp.processEvents()

    assert assistant_window.property("followLatest") is True
    assert assistant_window.property("hasUnreadIncoming") is False
    assert new_messages_button.property("visible") is False
    root.close()


def test_assistant_chat_wheel_scrolls_up_and_back_down_without_forcing_latest(
    qapp,
) -> None:
    """Break caught: real wheel input is swallowed or auto-follow overrides it."""
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    assistant_window = _assistant_window(root)
    message_list = _visual_item(assistant_window, "assistantMessageList")
    new_messages_button = _visual_item(assistant_window, "assistantNewMessagesButton")
    assert message_list is not None and new_messages_button is not None

    for index in range(60):
        assistant.messages.append(
            AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, f"第 {index} 条滚轮回复。")
        )
    for _ in range(5):
        qapp.processEvents()

    def send_wheel(delta: int) -> None:
        position = message_list.mapToItem(
            assistant_window.contentItem(),
            QPointF(message_list.width() / 2, message_list.height() / 2),
        )
        event = QWheelEvent(
            position,
            position,
            QPoint(0, 0),
            QPoint(0, delta),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        assert QCoreApplication.sendEvent(assistant_window, event) is True
        for _ in range(3):
            qapp.processEvents()

    bottom = float(message_list.property("contentHeight")) - float(message_list.height())
    assert float(message_list.property("contentY")) >= bottom - 1
    assert assistant_window.property("followLatest") is True

    send_wheel(120)
    scrolled_up = float(message_list.property("contentY"))
    assert scrolled_up > 0
    assert scrolled_up < bottom
    assert assistant_window.property("followLatest") is False

    assistant.messages.append(
        AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, "滚轮期间的新消息。")
    )
    for _ in range(3):
        qapp.processEvents()
    QTest.qWait(50)
    assert float(message_list.property("contentY")) <= scrolled_up + 1
    assert assistant_window.property("followLatest") is False
    assert assistant_window.property("userIsAtBottom") is False
    assert assistant_window.property("hasUnreadIncoming") is True
    assert new_messages_button.property("visible") is True

    send_wheel(-120)
    assert float(message_list.property("contentY")) > scrolled_up
    root.close()


def test_provider_and_model_popups_use_dark_theme_in_all_states(qapp):
    from tests.qt.test_studio_workflows import click_item, qml
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    assistant = engine.rootContext().contextProperty('assistant')
    assistant.openWindow(); qapp.processEvents()
    window = _assistant_window(root)
    theme = window.property('theme')
    selector = _visual_item(window, 'studioAssistantModelSelector')
    click_item(window, selector); QTest.qWait(60)
    assert qml(selector, 'popup.opened')
    assert qml(selector, 'popup.contentItem.count') > 0
    assert qml(selector, 'popup.background.color.toString()') == theme.property('popupBackground').name()
    qml(selector, 'popup.close(); true')
    click_item(window, _visual_item(window, 'studioAssistantConnectionSettings')); QTest.qWait(60)
    pane = window.findChild(QObject, 'studioAIServicePane')
    assert pane is not None
    assert _visual_item(window, 'studioAIConnection-openai') is not None
    for token in ('popupBackground', 'surfaceHover', 'accentSelection', 'surface'):
        assert theme.property(token).name().lower() != '#ffffff'
    root.close()
