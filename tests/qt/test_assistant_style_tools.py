"""Actual Studio model-tool integration, isolated profile and in-memory audio."""
from PySide6.QtCore import QObject
from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.music_agent.agent_loop import MusicAgentLoop
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.music_agent.test_assistant_controller import _SerializedLoopProvider
from tests.music_agent.test_model_resource_tools import call
from tools.studio_evidence import install_memory_output


def test_in_app_model_style_tool_changes_real_qml_and_persists(studio_session, qtbot, monkeypatch):
    blocked=install_memory_output(monkeypatch.setattr)
    app,engine,root,editor,playback=studio_session
    initialize_deferred_services(engine)
    assistant=engine.rootContext().contextProperty('assistant')
    assert assistant._appearance_tools is not None
    before_color=root.color().name()
    before_music=editor.state
    provider=_SerializedLoopProvider([
        call('get_ui_style'),
        call('apply_ui_style',name='午夜青玉',settings=dict(themeId='ink',customThemeColor='#28564c',
             themeSeedSource='custom',adaptiveTheme=True,customNoteColor='#8bc7aa',noteColorSource='custom',noteStyle='rounded')),
    ])
    assistant._agent_loop_factory=lambda **kwargs:MusicAgentLoop(provider=provider,**kwargs)
    assert assistant.sendMessage('把软件主题改成深色青玉风格，直接应用并保存')
    qtbot.waitUntil(lambda:not assistant.isBusy,timeout=10000)
    qtbot.waitUntil(lambda:root.color().name()!=before_color)
    appearance=engine.rootContext().contextProperty('studioAppearance')
    assert appearance.values['customNoteColor']=='#8bc7aa'
    assert editor.state==before_music and not playback.playing
    assert any(p['name']=='午夜青玉' for p in assistant._appearance_tools.execute('get_ui_style',{}).data['presets'])
    assert not blocked
