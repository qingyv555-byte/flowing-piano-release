"""The in-app model designs and saves actual Studio styles through Qt APIs."""
import importlib
import importlib.util
from PySide6.QtCore import QSettings
from ai_piano_studio.appearance.studio_settings import StudioAppearance
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from tests.music_agent.test_creation_workflow import assistant_for, done
from tests.music_agent.test_model_resource_tools import call


def style_module():
    assert importlib.util.find_spec('ai_piano_studio.music_agent.style_tools'), 'The model cannot design Studio skins'
    return importlib.import_module('ai_piano_studio.music_agent.style_tools')


def test_partial_skin_patch_schema_keeps_optional_fields_compatible_with_api_tools():
    module=style_module()
    from ai_piano_studio.music_agent.tool_registry import MusicToolRegistry
    registry=module.with_appearance_tools(MusicToolRegistry.default())
    schemas={t['function']['name']:t['function'] for t in registry.openai_schemas()}
    assert schemas['apply_ui_style']['strict'] is False
    assert schemas['get_notes']['strict'] is True


def test_model_designs_persists_and_undoes_skin_without_editing_music(tmp_path, qapp):
    module = style_module()
    appearance = StudioAppearance(QSettings(str(tmp_path/'appearance.ini'), QSettings.IniFormat), tmp_path/'backgrounds')
    before_style = dict(appearance.values)
    editor = EditorController(ProjectState(notes=(Note.create(id='old', pitch=60, start_time=0, duration=1, velocity=80),)))
    before_music = editor.state
    assistant, _, provider = assistant_for(tmp_path, editor, [
        call('get_ui_style'),
        call('apply_ui_style', name='墨绿玉石', settings=dict(themeId='ink', customThemeColor='#24564c',
             themeSeedSource='custom', adaptiveTheme=True, customNoteColor='#75b89a', noteColorSource='custom', noteStyle='rounded')),
    ])
    assistant._appearance_tools = module.AppearanceTools(appearance)
    assert assistant.sendMessage('给软件设计一套墨绿色玉石风格皮肤，直接用上')
    done(qapp, assistant)
    assert appearance.values['themeId'] == 'ink', (assistant.taskProgress.get('safeErrorCode'),
        [m.text for m in assistant.messages.entries],len(provider.requests), provider.requests[-1][-1])
    assert appearance.values['customNoteColor'] == '#75b89a'
    assert editor.state == before_music
    reloaded = StudioAppearance(QSettings(str(tmp_path/'appearance.ini'), QSettings.IniFormat), tmp_path/'backgrounds')
    assert reloaded.values['customNoteColor'] == '#75b89a'
    saved = assistant._appearance_tools.execute('get_ui_style', {})
    assert any(p['name']=='墨绿玉石' for p in saved.data['presets'])
    provider._scripts.append(call('restore_ui_style', preset_id='previous'))
    assert assistant.sendMessage('恢复刚才改皮肤之前的样式')
    done(qapp, assistant)
    assert dict(appearance.values) == before_style
    assert editor.state == before_music
    assistant.shutdown()


def test_style_change_rejects_unknown_fields_and_preserves_open_settings_draft(tmp_path, qapp):
    module = style_module()
    appearance = StudioAppearance(QSettings(str(tmp_path/'appearance.ini'), QSettings.IniFormat), tmp_path/'backgrounds')
    service = module.AppearanceTools(appearance)
    before = dict(appearance.values)
    result = service.execute('apply_ui_style', dict(name='bad', settings={'qml':'malicious code'}))
    assert result.data['status']=='unavailable' and dict(appearance.values)==before
    appearance.beginPreview()
    appearance.setValue('themeId','ink')
    draft = dict(appearance.values)
    result = service.execute('apply_ui_style', dict(name='other',settings={'themeId':'paper'}))
    assert result.data['status']=='busy' and dict(appearance.values)==draft
    appearance.cancelPreview()


def test_busy_style_action_does_not_force_music_write_or_claim_application(tmp_path,qapp):
    module=style_module()
    from ai_piano_studio.music_agent.providers import ProviderEvent
    appearance=StudioAppearance(QSettings(str(tmp_path/'appearance.ini'),QSettings.IniFormat),tmp_path/'backgrounds')
    appearance.beginPreview()
    appearance.setValue('customThemeColor','#112233')
    old=Note.create(id='old',pitch=60,start_time=0,duration=1,velocity=80)
    editor=EditorController(ProjectState(notes=(old,)))
    assistant,_,provider=assistant_for(tmp_path,editor,[call('apply_ui_style',name='另一套',settings={'themeId':'ink'}),
        (ProviderEvent(text_delta='已把皮肤应用并保存。',finish_reason='stop'),)])
    assistant._appearance_tools=module.AppearanceTools(appearance)
    assert assistant.sendMessage('把当前界面改成深色，直接应用')
    done(qapp,assistant)
    assert len(provider.requests)==2
    assert editor.state.active_notes==(old,)
    assert appearance.values['themeId']=='paper'
    assert assistant.taskProgress['status']=='failed'
    assert any('未应用' in m.text for m in assistant.messages.entries)
    assert not any(m.text=='已把皮肤应用并保存。' for m in assistant.messages.entries)
    appearance.cancelPreview()
    assistant.shutdown()
