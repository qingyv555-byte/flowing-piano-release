"""UI-thread bridge for a model-directed Studio session."""
from dataclasses import asdict, is_dataclass
import inspect
from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine

from ai_piano_studio.music_agent.tool_contracts import MusicPermission, MusicExecutionMode, MusicToolResult


def live_object(assistant, path):
    if path.startswith('ui:'):
        name, _, suffix = path[3:].partition('.')
        engine = assistant.parent()
        roots = engine.rootObjects() if hasattr(engine, 'rootObjects') else []
        roots += [root for view in assistant._creator_views for root in view._creator_roots]
        node = None
        for root in roots:
            if root.objectName() == name:
                node = root
            else:
                node = root.findChild(QObject, name)
            if node is not None:
                break
        if node is None:
            raise ValueError(f'UI object not found: {name}')
        parts = suffix.split('.') if suffix else []
    else:
        root, *parts = path.split('.')
        node = {'assistant': assistant, 'editor': assistant._editor, 'playback': assistant._analysis_playback,
                'proposal': assistant._proposal_manager, 'ui': assistant.parent()}[root]
    for part in parts:
        if isinstance(node, dict):
            node = node[part]
        elif isinstance(node, QObject) and node.metaObject().indexOfProperty(part) >= 0:
            node = node.property(part)
        else:
            node = getattr(node, part)
    return node


def describe(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, MusicToolResult):
        return dict(success=value.success, summary=value.summary, error_code=value.error_code, data=dict(value.data))
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): describe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [describe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if callable(value):
        try:
            signature = str(inspect.signature(value))
        except (TypeError, ValueError):
            signature = 'See application source for signature'
        return dict(callable=True, signature=signature, documentation=inspect.getdoc(value))
    result = dict(type=type(value).__name__, attributes=dir(value))
    if isinstance(value, QObject):
        meta = value.metaObject()
        result['properties'] = [meta.property(i).name() for i in range(meta.propertyCount())]
        result['methods'] = [bytes(meta.method(i).methodSignature()).decode() for i in range(meta.methodCount())]
        result['children'] = [dict(object_name=c.objectName(), type=c.metaObject().className()) for c in value.children()]
    return result


def fresh_context(assistant):
    return assistant._gateway.create_execution_context(authorized_bars=(), permissions=frozenset(MusicPermission),
        mode=MusicExecutionMode.PROPOSAL, intent_template='studio_creator')


def app_operation(assistant, name, payload, context):
    try:
        if name == 'report_progress':
            assistant._task_progress.report_model(assistant._active_generation, payload)
            result = dict(displayed=True, **payload)
        elif name == 'inspect_app':
            result = dict(path=payload['path'], value=describe(live_object(assistant, payload['path'])))
        elif name == 'call_app':
            function = live_object(assistant, payload['path'])
            result = dict(path=payload['path'], result=describe(function(*payload.get('args', []), **payload.get('kwargs', {}))))
        elif name == 'set_app_property':
            owner, _, key = payload['path'].rpartition('.')
            node = live_object(assistant, owner)
            if isinstance(node, QObject) and node.metaObject().indexOfProperty(key) >= 0:
                if not node.setProperty(key, payload['value']):
                    raise ValueError('Qt rejected the property value')
            elif isinstance(node, dict):
                node[key] = payload['value']
            else:
                setattr(node, key, payload['value'])
            result = dict(path=payload['path'], value=describe(live_object(assistant, payload['path'])))
        elif name == 'apply_music_changes':
            assistant._proposal_manager.register(context)
            receipt = assistant._proposal_manager.apply_proposal()
            if not receipt.success:
                return receipt, context
            result = describe(receipt)
            context = fresh_context(assistant)
        elif name == 'open_app_view':
            path = assistant._creator_workspace.resolve(payload['path'])
            engine = QQmlApplicationEngine(assistant)
            for key, node in (('editor', assistant._editor), ('assistant', assistant), ('playback', assistant._analysis_playback)):
                if node is not None:
                    engine.rootContext().setContextProperty(key, node)
            engine.load(QUrl.fromLocalFile(str(path)))
            engine._creator_roots = engine.rootObjects()
            if not engine._creator_roots:
                engine.deleteLater()
                raise ValueError('QML did not create a root object; check QML imports and syntax')
            assistant._creator_views.append(engine)
            result = dict(opened=True, path=str(path), root_count=len(engine.rootObjects()))
        else:
            raise ValueError('Unknown live application operation')
        if (context.project_identity != f'session:{assistant._editor.projectSessionId}'
                or context.baseline_revision != assistant._editor.projectRevision):
            context = fresh_context(assistant)
        assistant._active_context = context
        return MusicToolResult.success_result(name, 'Application operation executed.', data=result), context
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as error:
        return MusicToolResult.failure(name, 'execution_error', str(error), data=dict(exception=type(error).__name__)), context
