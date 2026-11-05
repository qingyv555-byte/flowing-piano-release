"""Model-designed Studio skins using the application's actual settings API."""
from __future__ import annotations
import hashlib
import json
import math
import re

from ai_piano_studio.appearance.studio_settings import DEFAULTS, _CHOICES
from ai_piano_studio.persistence.atomic import atomic_write_bytes
from ai_piano_studio.music_agent.tool_contracts import MusicPermission, MusicToolResult
from ai_piano_studio.music_agent.tool_registry import MusicToolDefinition, MusicToolRegistry


STYLE_PROPERTIES = {}
for _key, _value in DEFAULTS.items():
    if _key == 'backgroundImportSize':
        continue
    if _key in {'homeSource', 'editorSource'}:
        _schema = dict(type='string', enum=['builtin','none'])
    elif _key in _CHOICES:
        _schema = dict(type='string', enum=sorted(_CHOICES[_key]))
    elif _key in {'customThemeColor','customNoteColor'}:
        _schema = dict(type='string', pattern='^#[0-9a-fA-F]{6}$')
    elif isinstance(_value, bool):
        _schema = dict(type='boolean')
    elif _key == 'motionFps':
        _schema = dict(type='integer', enum=[15,30,60])
    else:
        _schema = dict(type='number', minimum=0, maximum=1)
    STYLE_PROPERTIES[_key] = _schema


class _AppearanceToolDefinition(MusicToolDefinition):
    def openai_schema(self):
        schema = super().openai_schema()
        if self.name == 'apply_ui_style':
            # A skin is a partial settings patch. Optional nested properties
            # are incompatible with API strict mode's all-properties-required
            # rule. The application still validates every field and value.
            schema['function']['strict'] = False
        return schema


STYLE_DEFINITIONS = (
    MusicToolDefinition('get_ui_style',
        'Read current Studio skin, supported setting keys/values and reusable user presets. '
        'Use before designing a skin; inspect real capabilities instead of inventing unsupported styles.',
        dict(type='object',properties={},required=[],additionalProperties=False),
        MusicPermission.MANAGE_APP_APPEARANCE, True),
    _AppearanceToolDefinition('apply_ui_style',
        'Design and directly apply a requested Studio skin from supported theme colors, note colors/material, '
        'background visibility, spectrum, motion and feedback parameters. Saves a reusable named preset. '
        'Music and account settings are unchanged. Cannot execute QML/scripts or generate artwork. '
        'Specify themeSeedSource=custom and adaptiveTheme=true when using a custom theme color.',
        dict(type='object',properties=dict(name=dict(type='string',minLength=1,maxLength=80),
            settings=dict(type='object',properties=STYLE_PROPERTIES,required=[],additionalProperties=False,minProperties=1)),
            required=['name','settings'],additionalProperties=False),
        MusicPermission.MANAGE_APP_APPEARANCE, False),
    MusicToolDefinition('restore_ui_style',
        'Restore the previous AI style change using preset_id=previous, or apply a saved preset ID from get_ui_style.',
        dict(type='object',properties=dict(preset_id=dict(type='string',minLength=1,maxLength=64)),
            required=['preset_id'],additionalProperties=False),
        MusicPermission.MANAGE_APP_APPEARANCE, False),
)
STYLE_NAMES = frozenset(d.name for d in STYLE_DEFINITIONS)


def with_appearance_tools(registry):
    return MusicToolRegistry([*(registry.lookup(n) for n in registry.names),*STYLE_DEFINITIONS])


class AppearanceTools:
    def __init__(self, appearance):
        self.appearance = appearance
        self.home = appearance._asset_home.parent/'assistant'/'styles'
        self.previous = None

    @staticmethod
    def _public_values(values):
        return {k:('local-image' if k in {'homeSource','editorSource'} and v not in {'builtin','none'} else v)
                for k,v in values.items() if k in STYLE_PROPERTIES}

    def _normalize(self, settings):
        if not isinstance(settings, dict) or not settings or set(settings)-set(STYLE_PROPERTIES):
            raise ValueError('Use only supported Studio appearance keys from get_ui_style')
        normalized = {}
        for key,value in settings.items():
            schema = STYLE_PROPERTIES[key]
            expected = schema['type']
            if expected == 'boolean' and not isinstance(value,bool):
                raise ValueError('Boolean style setting required: '+key)
            if expected in {'number','integer'} and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):
                raise ValueError('Finite numeric style setting required: '+key)
            if expected == 'string' and not isinstance(value,str):
                raise ValueError('String style setting required: '+key)
            if 'enum' in schema and value not in schema['enum']:
                raise ValueError('Unsupported style choice: '+key)
            if key in {'customThemeColor','customNoteColor'} and not re.fullmatch(r'#[0-9a-fA-F]{6}',value):
                raise ValueError('Use a six-digit hex color for '+key)
            result = self.appearance._normalize(key,value)
            if isinstance(value,(int,float)) and not isinstance(value,bool) and result!=value:
                raise ValueError('Style value exceeds the supported range: '+key)
            normalized[key] = result
        return normalized

    def _read(self):
        presets, unreadable = [], 0
        for path in sorted(self.home.glob('*.json'))[:100]:
            if not re.fullmatch(r'[0-9a-f]{16}\.json',path.name):
                continue
            try:
                if path.stat().st_size>20000:
                    raise ValueError('Oversized preset')
                preset=json.loads(path.read_text(encoding='utf8'))
                if not isinstance(preset.get('name'),str):
                    raise ValueError('Invalid preset name')
                self._normalize(preset['settings'])
                presets.append(dict(id=path.stem,name=preset['name'][:80]))
            except (OSError,ValueError,KeyError,TypeError):
                unreadable += 1
        return dict(status='available',values=self._public_values(self.appearance.values),
                    supported=STYLE_PROPERTIES,presets=presets,unreadable_presets=unreadable,
                    can_restore_previous=self.previous is not None)

    def execute(self, name, payload):
        if name=='get_ui_style':
            return MusicToolResult.success_result(name,'Actual Studio appearance and supported settings.',data=self._read())
        if self.appearance._preview or self.appearance.importBusy:
            return MusicToolResult.success_result(name,'An appearance dialog draft or import is active; preserved it.',data=dict(status='busy'))
        try:
            if name=='apply_ui_style':
                label=payload['name']
                if not isinstance(label,str) or not 1<=len(label.strip())<=80:
                    raise ValueError('A skin name of 1-80 characters is required')
                settings=self._normalize(payload['settings'])
                preset_id=hashlib.sha256(label.strip().encode()).hexdigest()[:16]
                path=self.home/(preset_id+'.json')
                old_file=path.read_bytes() if path.is_file() else None
                atomic_write_bytes(path,json.dumps(dict(version=1,name=label.strip(),settings=settings),ensure_ascii=False).encode())
            elif name=='restore_ui_style':
                preset_id=payload['preset_id']
                if preset_id=='previous':
                    if self.previous is None:
                        raise ValueError('No previous AI style change is available in this session')
                    # This is the application's own snapshot, including private
                    # background asset URLs; never expose those paths to the model.
                    settings=dict(self.previous)
                else:
                    if not isinstance(preset_id,str) or not re.fullmatch(r'[0-9a-f]{16}',preset_id):
                        raise ValueError('Use a preset ID from get_ui_style')
                    path=self.home/(preset_id+'.json')
                    if path.stat().st_size>20000:
                        raise ValueError('Oversized preset')
                    settings=self._normalize(json.loads(path.read_text(encoding='utf8'))['settings'])
            else:
                raise ValueError('Unknown appearance tool')
            before=dict(self.appearance.values)
            self.appearance.beginPreview()
            for key,value in settings.items():
                self.appearance.setValue(key,value)
            if not self.appearance.applyPreview():
                self.appearance.cancelPreview()
                if name=='apply_ui_style':
                    if old_file is not None:
                        atomic_write_bytes(path,old_file)
                    else:
                        path.unlink(missing_ok=True)
                raise ValueError('Studio could not save the appearance; previous style retained')
            self.previous=before
            return MusicToolResult.success_result(name,'Studio skin applied and saved; music was unchanged.',
                data=dict(status='applied',preset_id=preset_id,values=self._public_values({k:self.appearance.values[k] for k in settings}),
                          music_modified=False))
        except (ValueError,OSError,KeyError,TypeError) as error:
            return MusicToolResult.success_result(name,'Style action could not be completed.',
                data=dict(status='unavailable',reason=type(error).__name__ if isinstance(error,OSError) else str(error)[:200]))


def execute_appearance_tool(service, gateway, name, arguments, context):
    from ai_piano_studio.music_agent.tool_gateway import (
        MusicToolValidationError, MusicToolPermissionError, MusicToolConflictError, MusicToolLimitError,
    )
    definition=next(d for d in STYLE_DEFINITIONS if d.name==name)
    try:
        gateway._require_current_context(context)
        gateway._require_permission(definition,context)
        payload=gateway._validate_schema(definition.input_schema,arguments)
        context.budget.consume_step()
        return service.execute(name,payload)
    except MusicToolValidationError:
        return MusicToolResult.failure(name,'validation_error','Invalid style parameters; read get_ui_style first.')
    except MusicToolPermissionError:
        return MusicToolResult.failure(name,'permission_denied','Appearance tools are not enabled in this session.')
    except MusicToolConflictError:
        return MusicToolResult.failure(name,'conflict','Active project changed before the appearance action.')
    except MusicToolLimitError:
        return MusicToolResult.failure(name,'limit_exceeded','Appearance operation budget exceeded.')
