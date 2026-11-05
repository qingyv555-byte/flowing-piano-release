"""Native protocol seam, using an owned subprocess without model inference."""
import json
import os
import subprocess
import sys
import time
import threading
import base64
import hashlib
import struct
import zlib

import pytest

from ai_piano_studio.music_agent import codex_native as native
from ai_piano_studio.music_agent.providers import ProviderError


TOOLS = [{'type': 'function', 'function': {'name': 'read_music',
          'description': 'Read notes', 'parameters': {'type': 'object'}}}]
MESSAGES = [{'role': 'system', 'content': 'Use validated music tools.'},
            {'role': 'user', 'content': 'Prepare a draft'}]


def png_bytes(width=1, height=1, padding=0):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width,height,8,0,0,0,0))
            + (chunk(b'tEXt', b'x' * padding) if padding else b'')
            + chunk(b'IDAT', zlib.compress(b'\x00\x00')) + chunk(b'IEND', b''))


@pytest.fixture
def server(tmp_path, monkeypatch):
    script = tmp_path / 'server.py'
    trace = tmp_path / 'trace.jsonl'
    script.write_text('''import json,sys,time,os
trace=open(sys.argv[1], 'a', buffering=1)
mode=sys.argv[2]
def send(x):
 print(json.dumps(x), flush=True)
def call(n):
 send({'id': [] if mode=='bad_id' else 100+n,'method':'item/tool/call','params':{'threadId':'foreign' if mode=='foreign' else 'th','turnId':'tu','callId':str(n),'tool':[] if mode=='bad_tool' else 'unknown' if mode=='unknown' else 'read_music','arguments':[] if mode=='arguments' else {'bar':n}}})
for line in sys.stdin:
 m=json.loads(line); trace.write(json.dumps(m)+'\\n')
 method=m.get('method')
 if method=='initialize': send({'id':m['id'],'result':{'userAgent':'fixture'}})
 elif method=='model/list':
  if mode=='model_error': send({'id':m['id'],'error':{'code':-32601,'message':'PRIVATE METADATA ERROR'}})
  elif mode=='model_missing': send({'id':m['id'],'result':{'data':[],'nextCursor':None}})
  elif mode=='model_other': send({'id':m['id'],'result':{'data':[{'id':'other-model','model':'other-model','isDefault':True,'defaultReasoningEffort':'ultra'}],'nextCursor':None}})
  elif mode=='model_no_default': send({'id':m['id'],'result':{'data':[{'id':'test-model','model':'test-model'}],'nextCursor':None}})
  elif mode=='model_page' and not m['params'].get('cursor'): send({'id':m['id'],'result':{'data':[{'id':'other-model','model':'other-model','defaultReasoningEffort':'high'}],'nextCursor':'second'}})
  else: send({'id':m['id'],'result':{'data':[{'id':'test-model','model':'test-model','defaultReasoningEffort':'low','supportedReasoningEfforts':[{'reasoningEffort':'low'}]}],'nextCursor':None}})
 elif method=='config/read': send({'id':m['id'],'result':{'config':{'mcp_servers':{'inherited':{'enabled':True}},'plugins':{'example@local':{'enabled':True}}}}})
 elif method=='thread/start':
  cwd=m['params']['cwd']
  sandbox={'type':'workspaceWrite','networkAccess':False,'writableRoots':[],
           'excludeTmpdirEnvVar':True,'excludeSlashTmp':True}
  if m['params']['sandbox']=='danger-full-access': sandbox={'type':'dangerFullAccess'}
  if mode=='unsafe_sandbox': sandbox['writableRoots']=[os.path.dirname(cwd)]
  send({'id':m['id'],'result':{'thread':[] if mode=='bad_thread' else {'id':'th'},
       'cwd':cwd,'runtimeWorkspaceRoots':[cwd],'sandbox':sandbox,'approvalPolicy':'never'}})
  if mode=='blocked_write': time.sleep(30)
 elif method=='turn/start':
  send({'id':m['id'],'result':{'turn':[] if mode=='bad_turn' else {'id':'tu'}}})
  if mode=='timeout': time.sleep(30)
  elif mode=='eof': sys.exit(0)
  elif mode=='bytes': print('x'*5000000, flush=True)
  elif mode=='large_text': send({'method':'item/agentMessage/delta','params':{'threadId':'th','turnId':'tu','delta':'x'*1100000}})
  elif mode=='approval': send({'id':90,'method':'item/commandExecution/requestApproval','params':{}})
  elif mode=='user_input': send({'id':90,'method':'item/tool/requestUserInput','params':{'questions':[{'id':'mood','question':'Mood?','options':[]}]}})
  elif mode=='terminal_error': send({'method':'error','params':{'threadId':'th','turnId':'tu','willRetry':False,'error':{'message':'PRIVATE SERVER ERROR'}}})
  elif mode=='bad_params': send({'method':'item/completed','params':None})
  elif mode=='bad_item': send({'method':'item/completed','params':{'item':[]}})
  elif mode=='bad_method': send({'id':90,'method':[],'params':{}})
  elif mode=='bad_completed': send({'method':'turn/completed','params':{'threadId':'th','turn':[]}})
  elif mode=='bad_thread_id': send({'method':'item/agentMessage/delta','params':{'threadId':[],'delta':'bad'}})
  else:
   if mode=='successful_mcp':
    send({'method':'item/completed','params':{'threadId':'th','turnId':'tu','item':{'type':'mcpToolCall','status':'completed'}}})
   if mode=='native_tools':
    for kind,status,extra in [('commandExecution','inProgress',{}),
                              ('commandExecution','completed',{'exitCode':7,'aggregatedOutput':'PRIVATE OUTPUT'}),
                              ('fileChange','completed',{}),('mcpToolCall','failed',{'error':{'message':'PRIVATE ERROR'}})]:
     send({'method':'item/started' if status=='inProgress' else 'item/completed',
           'params':{'threadId':'th','turnId':'tu','item':{'type':kind,'status':status,**extra}}})
   if mode in {'orphan_helper','live_helper'}:
    import subprocess
    helper=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    open(sys.argv[1]+'.helper','w').write(str(helper.pid))
   if mode.startswith('retry'):
    info={'retry_stream':{'responseStreamDisconnected':{'httpStatusCode':None}},'retry_rate':'rateLimitExceeded','retry_server':'serverOverloaded','retry_429':{'responseStreamConnectionFailed':{'httpStatusCode':429}},'retry_503':{'httpConnectionFailed':{'httpStatusCode':503}},'retry_unknown':{'futureError':'PRIVATE UNKNOWN DETAIL'}}.get(mode)
    send({'method':'error','params':{'threadId':'th','turnId':'tu','willRetry':True,'error':{'message':'PRIVATE SERVER ERROR','additionalDetails':'PRIVATE DETAILS','codexErrorInfo':info}}})
   if mode=='public_actions':
    actions=[{'type':'search','query':'public query '+('q'*600),'queries':['repository midi','sheet music'],'reasoning':'PRIVATE REASON','extra':'PRIVATE EXTRA'}, {'type':'openPage','url':'https://example.org/scores/song?token=PRIVATE_TOKEN#PRIVATE_FRAGMENT','body':'PRIVATE BODY'}, {'type':'findInPage','url':'http://127.0.0.1/private','pattern':'PRIVATE PATTERN'}, {'type':'openPage','url':'https://PRIVATE_USER:PRIVATE_PASSWORD@example.org/score'}, {'type':'openPage','url':'file:///PRIVATE_LOCAL_FILE'}, {'type':'unknown','query':'PRIVATE UNKNOWN'}]
    send({'method':'item/started','params':{'threadId':'th','turnId':'tu','item':{'type':'webSearch','action':{'type':'search','query':'PRIVATE STARTED'}}}})
    for action in actions:
     send({'method':'item/completed','params':{'threadId':'th','turnId':'tu','item':{'type':'webSearch','action':action,'reasoning':'PRIVATE ITEM'}}})
   if mode=='many_actions':
    for n in range(60):
     send({'method':'item/completed','params':{'threadId':'th','turnId':'tu','item':{'type':'webSearch','action':{'type':'search','queries':['q'*700]*60}}}})
   if mode=='public_urls':
    for url in ['http://localhost/private','http://10.0.0.1/private','http://127.1/private','https://[::1]/private','http://2130706433/private','https://example.local/private','https://example.org:bad/private','https://example.org/public?signature=PRIVATE#PRIVATE','http://8.8.8.8/public?token=PRIVATE','https://[2606:4700:4700::1111]/public?key=PRIVATE']:
     send({'method':'item/completed','params':{'threadId':'th','turnId':'tu','item':{'type':'webSearch','action':{'type':'openPage','url':url}}}})
   send({'method':'item/completed','params':{'threadId':'th','turnId':'tu','item':{'type':'webSearch','id':'w','status':'completed'}}})
   send({'method':'item/reasoning/textDelta','params':{'delta':'HIDDEN REASONING'}})
   call(1)
   if mode=='orphan_helper': sys.exit(0)
 elif m.get('id')==90 and 'result' in m and mode in {'approval','user_input'}: call(1)
 elif m.get('id')==101 and 'result' in m:
  if mode.startswith('image_echo'):
   content=m['result']['contentItems']
   if mode=='image_echo_tampered': content[1]['imageUrl'] += 'A'
   send({'method':'item/completed','params':{'threadId':'th','turnId':'tu','item':{'type':'dynamicToolCall','id':'wrong' if mode=='image_echo_wrong_call' else '1','tool':'read_music','status':'completed','contentItems':content}}})
   if mode=='image_echo_duplicate':
    send({'method':'item/completed','params':{'threadId':'th','turnId':'tu','item':{'type':'dynamicToolCall','id':'1','tool':'read_music','status':'completed','contentItems':content}}})
   if mode=='image_echo_over_budget':
    for _ in range(5): send({'method':'item/agentMessage/delta','params':{'threadId':'th','turnId':'tu','delta':'x'*900000}})
  call(2)
 elif m.get('id')==102 and 'result' in m:
  if mode=='image_echo_multiple':
   send({'method':'item/completed','params':{'threadId':'th','turnId':'tu','item':{'type':'dynamicToolCall','id':'2','tool':'read_music','status':'completed','contentItems':m['result']['contentItems']}}})
  send({'method':'item/agentMessage/delta','params':{'threadId':'th','turnId':'tu','delta':'Draft ready'}})
  send({'method':'turn/completed','params':{'threadId':'th','turn':{'id':'tu','status':'completed'}}})
''', encoding='utf-8')
    real_popen = subprocess.Popen
    processes = []
    commands = []
    def launch(args, **kwargs):
        if args[0] != 'codex':
            return real_popen(args, **kwargs)
        kwargs['env'] = {**kwargs['env'], 'PYTHONIOENCODING': 'utf-8'}
        commands.append(args)
        proc = real_popen([sys.executable, '-u', str(script), str(trace), mode[0]], **kwargs)
        processes.append(proc)
        return proc
    mode = ['normal']
    monkeypatch.setattr(native.subprocess, 'Popen', launch)
    return mode, processes, commands, trace


def session(**kwargs):
    return native.CodexNativeSession('codex', 'test-model', env=dict(os.environ),
        timeout_seconds=kwargs.pop('timeout_seconds', 3),
        cancelled=kwargs.pop('cancelled', lambda: False), progress=lambda e: None,
        web_search=True, **kwargs)


def feedback(call_id):
    return [*MESSAGES, {'role': 'tool', 'tool_call_id': call_id, 'content': '{"notes":4}'}]


def test_native_tools_continue_one_process_and_turn(server):
    _, processes, commands, trace = server
    s = session()
    first = list(s.stream_events(MESSAGES, TOOLS, None))
    assert first[-1].tool_calls[0].call_id == '1'
    second = list(s.stream_events(feedback('1'), TOOLS, None))
    assert second[-1].tool_calls[0].call_id == '2'
    final = list(s.stream_events(feedback('2'), TOOLS, None))
    assert ''.join(e.text_delta for e in final) == 'Draft ready'
    assert s.complete and s.search_observed
    assert len(processes) == 1 and processes[0].poll() is not None
    records = [json.loads(x) for x in trace.read_text().splitlines()]
    assert sum(m.get('method') == 'turn/start' for m in records) == 1
    start = next(m['params'] for m in records if m.get('method') == 'thread/start')
    assert start['allowProviderModelFallback'] is False
    assert start['approvalPolicy'] == 'never'
    assert start['sandbox'] == 'workspace-write'
    assert 'environments' not in start
    assert start['config']['features.shell_tool'] is native.OWNED_SHELL_SUPPORTED
    assert start['config']['sandbox_workspace_write.network_access'] is False
    assert not os.path.exists(start['cwd'])
    assert start['dynamicTools'] == [{'type': 'function', 'name': 'read_music',
        'description': 'Read notes', 'inputSchema': {'type': 'object'}}]
    assert next(m for m in records if m.get('id') == 101)['result'] == {
        'contentItems': [{'type': 'inputText', 'text': '{"notes":4}'}], 'success': True}
    assert 'HIDDEN' not in repr(final) + repr(s.diagnostics)
    assert 'mcp_servers={}' in commands[0]
    assert start['config']['mcp_servers']['inherited']['enabled'] is False
    assert start['config']['plugins']['example@local']['enabled'] is False


def test_native_tool_completion_records_outcomes_and_continues_gateway(server, monkeypatch):
    # Exercise policy negotiation independently of the host OS; ownership is
    # covered by real Windows jobs and the separate unsupported-host gate.
    monkeypatch.setattr(native, 'OWNED_SHELL_SUPPORTED', True)
    server[0][0] = 'native_tools'
    events = []
    s = session()
    s.progress = events.append
    assert not s.capabilities['task_workspace_shell']
    try:
        assert list(s.stream_events(MESSAGES, TOOLS, None))[-1].tool_calls[0].call_id == '1'
        assert s.capabilities['task_workspace_shell']
        assert s.capabilities['task_workspace_file_changes']
        assert not s.capabilities['host_mcp']
        assert s.diagnostics['native_command_started'] == 1
        assert s.diagnostics['native_command_failed'] == 1
        assert s.diagnostics['native_command_succeeded'] == 0
        assert s.diagnostics['native_file_change_succeeded'] == 1
        assert s.diagnostics['native_mcp_failed'] == 1
        assert 'native_command_failed' in [event.detail_code for event in events]
        assert 'PRIVATE' not in repr(events) + repr(s.diagnostics)
    finally:
        s.close()


def test_native_shell_requires_verified_descendant_containment(server, monkeypatch):
    monkeypatch.setattr(native, 'OWNED_SHELL_SUPPORTED', False)
    server[0][0] = 'native_tools'
    s = session()
    with pytest.raises(ProviderError):
        list(s.stream_events(MESSAGES, TOOLS, None))
    assert not s.capabilities['task_workspace_shell']
    records = [json.loads(line) for line in server[3].read_text().splitlines()]
    start = next(row['params'] for row in records if row.get('method') == 'thread/start')
    assert start['config']['features.shell_tool'] is False
    assert all(process.poll() is not None for process in server[1])


def test_native_rejects_successful_host_connector_despite_disabled_policy(server):
    server[0][0] = 'successful_mcp'
    s = session()
    with pytest.raises(ProviderError) as failure:
        list(s.stream_events(MESSAGES, TOOLS, None))
    assert failure.value.code == 'native_denied_request'
    assert not s.capabilities['host_mcp']
    assert all(process.poll() is not None for process in server[1])


def test_native_refuses_broader_runtime_write_roots_before_turn(server):
    mode, _, _, trace = server
    mode[0] = 'unsafe_sandbox'
    s = session()
    with pytest.raises(ProviderError) as failure:
        list(s.stream_events(MESSAGES, TOOLS, None))
    assert failure.value.code == 'native_denied_request'
    assert not s.capabilities['task_workspace_shell']
    assert 'turn/start' not in trace.read_text()


def test_pending_tool_png_feedback_uses_native_images_once_in_same_turn(server):
    s = session()
    image = png_bytes()
    try:
        assert list(s.stream_events(MESSAGES, TOOLS, None))[-1].tool_calls[0].call_id == '1'
        s.set_tool_result_images('1', [image, image])
        list(s.stream_events(feedback('1'), TOOLS, None))
        list(s.stream_events(feedback('2'), TOOLS, None))
    finally:
        s.close()
    records = [json.loads(x) for x in server[3].read_text().splitlines()]
    result = next(m['result'] for m in records if m.get('id') == 101)
    assert result['contentItems'] == [
        {'type':'inputText','text':'{"notes":4}'},
        *[{'type':'inputImage','imageUrl':'data:image/png;base64,' + base64.b64encode(image).decode('ascii')}] * 2,
    ]
    assert next(m['result'] for m in records if m.get('id') == 102)['contentItems'] == [
        {'type':'inputText','text':'{"notes":4}'}]
    assert sum(m.get('method') == 'turn/start' for m in records) == 1
    assert s.diagnostics['image_results'] == 1 and s.diagnostics['image_count'] == 2
    assert all(type(value) is int for value in s.diagnostics.values())
    assert 'base64' not in repr(s.diagnostics) + repr(s.public_search_actions)


@pytest.mark.parametrize('invalid', [None, 'https://example.org/score.png',
    [b'not a PNG'], [png_bytes()[:20]], [png_bytes()[:-1]],
    [png_bytes()[:30] + b'bad CRC' + png_bytes()[37:]],
    [png_bytes(width=0)], [png_bytes(width=2201)], [png_bytes(width=2200,height=2000)],
    [png_bytes()] * 3, [png_bytes(padding=1_000_001)],
    [png_bytes(padding=900_000)] * 2, ['C:/private/file.png'],
])
def test_invalid_png_feedback_clears_previous_images_and_never_sends_media(server, invalid):
    s = session()
    try:
        list(s.stream_events(MESSAGES, TOOLS, None))
        s.set_tool_result_images('1', [png_bytes()])
        with pytest.raises(ProviderError) as error:
            s.set_tool_result_images('1', invalid)
        assert error.value.code == 'native_tool_images_invalid'
        list(s.stream_events(feedback('1'), TOOLS, None))
    finally:
        s.close()
    records = [json.loads(x) for x in server[3].read_text().splitlines()]
    result = next(m['result'] for m in records if m.get('id') == 101)
    assert result['contentItems'] == [{'type':'inputText','text':'{"notes":4}'}]
    assert s.diagnostics['image_results'] == s.diagnostics['image_count'] == 0


def test_png_feedback_rejects_wrong_call_and_clears_previous_media(server):
    s = session()
    try:
        with pytest.raises(ProviderError) as before_start:
            s.set_tool_result_images('1', [png_bytes()])
        assert before_start.value.code == 'native_tool_mismatch'
        list(s.stream_events(MESSAGES, TOOLS, None))
        s.set_tool_result_images('1', [png_bytes()])
        with pytest.raises(ProviderError) as wrong_call:
            s.set_tool_result_images('2', [png_bytes()])
        assert wrong_call.value.code == 'native_tool_mismatch'
        list(s.stream_events(feedback('1'), TOOLS, None))
    finally:
        s.close()
    records = [json.loads(x) for x in server[3].read_text().splitlines()]
    assert len(next(m['result']['contentItems'] for m in records if m.get('id') == 101)) == 1
    with pytest.raises(ProviderError) as after_close:
        s.set_tool_result_images('2', [png_bytes()])
    assert after_close.value.code == 'native_tool_mismatch'


def test_near_limit_png_feedback_fits_native_budget_and_does_not_repeat(server):
    server[0][0] = 'image_echo'
    s = session()
    try:
        list(s.stream_events(MESSAGES, TOOLS, None))
        image = png_bytes(padding=899_900)
        s.set_tool_result_images('1', (image,image))
        list(s.stream_events(feedback('1'), TOOLS, None))
        list(s.stream_events(feedback('2'), TOOLS, None))
        assert s.complete and s.diagnostics['image_count'] == 2
        assert s.diagnostics['image_echoes'] == 1
        assert 2_390_000 < s.diagnostics['received_bytes'] < 4_000_000
        assert 2_390_000 < s.diagnostics['sent_bytes'] < 4_000_000
    finally:
        s.close()


@pytest.mark.parametrize('mode', ['image_echo_tampered', 'image_echo_wrong_call', 'image_echo_duplicate'])
def test_oversized_native_image_echo_must_match_just_sent_media(server, mode):
    server[0][0] = mode
    s = session()
    try:
        list(s.stream_events(MESSAGES, TOOLS, None))
        s.set_tool_result_images('1', [png_bytes(padding=899_900)])
        with pytest.raises(ProviderError) as error:
            list(s.stream_events(feedback('1'), TOOLS, None))
        assert error.value.code == 'native_output_limit'
        assert not s.complete
    finally:
        s.close()


def test_ordinary_output_still_has_four_mb_budget_after_image_echo(server):
    server[0][0] = 'image_echo_over_budget'
    s = session()
    try:
        list(s.stream_events(MESSAGES, TOOLS, None))
        image = png_bytes(padding=899_900)
        s.set_tool_result_images('1', [image,image])
        with pytest.raises(ProviderError) as error:
            list(s.stream_events(feedback('1'), TOOLS, None))
        assert error.value.code == 'native_output_limit'
        assert s.diagnostics['image_echoes'] == 1
        assert s.diagnostics['received_bytes'] - s.diagnostics['received_media_bytes'] > 4_000_000
    finally:
        s.close()


def test_multiple_near_limit_png_rounds_use_separate_media_budget(server):
    server[0][0] = 'image_echo_multiple'
    image = png_bytes(padding=899_900)
    s = session()
    try:
        list(s.stream_events(MESSAGES, TOOLS, None))
        for call_id in ('1', '2'):
            s.set_tool_result_images(call_id, (image, image))
            list(s.stream_events(feedback(call_id), TOOLS, None))
        assert s.complete and s.diagnostics['image_echoes'] == 2
        for direction in ('sent', 'received'):
            media = s.diagnostics[f'{direction}_media_bytes']
            raw = s.diagnostics[f'{direction}_bytes']
            assert 4_000_000 < media < raw
            assert raw - media < 10_000
    finally:
        s.close()


def test_validated_media_cannot_exceed_aggregate_budget(server, monkeypatch):
    monkeypatch.setattr(native, 'MAX_NATIVE_MEDIA_BYTES', 4_000_000)
    server[0][0] = 'image_echo_multiple'
    image = png_bytes(padding=899_900)
    s = session()
    try:
        list(s.stream_events(MESSAGES, TOOLS, None))
        s.set_tool_result_images('1', (image, image))
        list(s.stream_events(feedback('1'), TOOLS, None))
        s.set_tool_result_images('2', (image, image))
        with pytest.raises(ProviderError) as error:
            list(s.stream_events(feedback('2'), TOOLS, None))
        assert error.value.code == 'context_too_large'
        assert 2_390_000 < s.diagnostics['sent_media_bytes'] < 4_000_000
        assert not s.complete
    finally:
        s.close()


def test_image_echo_preserves_new_expectation_installed_during_hash(monkeypatch):
    s = session()
    value = 'data:image/png;base64,' + base64.b64encode(png_bytes()).decode('ascii')
    original_hash = hashlib.sha256
    fingerprint = (len(value), original_hash(value.encode('ascii')).digest())
    old = {'thread': 'th', 'turn': 'tu', 'call': '1', 'tool': 'read_music',
           'images': (fingerprint,)}
    new = {**old, 'call': '2'}
    s._expected_image_echo = old

    def replace_expectation(data):
        with s._lock:
            s._expected_image_echo = new
        return original_hash(data)

    monkeypatch.setattr(native.hashlib, 'sha256', replace_expectation)
    item = {'type': 'dynamicToolCall', 'id': '1', 'tool': 'read_music',
            'status': 'completed', 'contentItems': [{'type': 'inputImage', 'imageUrl': value}]}
    message = {'method': 'item/completed',
               'params': {'threadId': 'th', 'turnId': 'tu', 'item': item}}
    try:
        assert s._strip_expected_image_echo(message) == len(value)
        assert s._expected_image_echo is new
        assert item['contentItems'] == []
        assert s.diagnostics['image_echoes'] == 1
    finally:
        s.close()


def test_unset_effort_uses_exact_model_default_once_for_native_task(server):
    s = session()
    try:
        list(s.stream_events(MESSAGES, TOOLS, None))
        list(s.stream_events(feedback('1'), TOOLS, None))
        list(s.stream_events(feedback('2'), TOOLS, None))
    finally:
        s.close()
    records = [json.loads(x) for x in server[3].read_text().splitlines()]
    turn = next(m['params'] for m in records if m.get('method') == 'turn/start')
    assert turn['effort'] == 'low'
    assert turn.get('model', 'test-model') == 'test-model'
    assert sum(m.get('method') == 'model/list' for m in records) == 1
    assert s.diagnostics['model_default_effort_used'] == 1


@pytest.mark.parametrize('mode', ['model_error', 'model_missing', 'model_other', 'model_no_default'])
def test_missing_model_metadata_leaves_effort_unset_without_substitution(server, mode):
    server[0][0] = mode
    s = session()
    try:
        assert list(s.stream_events(MESSAGES, TOOLS, None))[-1].tool_calls
    finally:
        s.close()
    records = [json.loads(x) for x in server[3].read_text().splitlines()]
    start = next(m['params'] for m in records if m.get('method') == 'thread/start')
    turn = next(m['params'] for m in records if m.get('method') == 'turn/start')
    assert start['model'] == 'test-model'
    assert 'effort' not in turn
    assert not s.diagnostics['model_default_effort_used']


def test_explicit_effort_skips_catalog_and_is_preserved(server):
    s = session()
    try:
        list(s.stream_events(MESSAGES, TOOLS, 'high'))
    finally:
        s.close()
    records = [json.loads(x) for x in server[3].read_text().splitlines()]
    assert not any(m.get('method') == 'model/list' for m in records)
    assert next(m['params'] for m in records if m.get('method') == 'turn/start')['effort'] == 'high'


def test_model_default_lookup_follows_catalog_pagination(server):
    server[0][0] = 'model_page'
    s = session()
    try:
        list(s.stream_events(MESSAGES, TOOLS, None))
    finally:
        s.close()
    records = [json.loads(x) for x in server[3].read_text().splitlines()]
    assert sum(m.get('method') == 'model/list' for m in records) == 2
    assert next(m['params'] for m in records if m.get('method') == 'turn/start')['effort'] == 'low'


@pytest.mark.parametrize('mode,category,http_status', [
    ('retry_stream', 'stream', None), ('retry_rate', 'rate_limit', None),
    ('retry_server', 'server', None), ('retry_429', 'rate_limit', 429),
    ('retry_503', 'server', 503), ('retry_unknown', 'unknown', None),
])
def test_retry_diagnostics_use_only_public_enum_and_http_status(server, mode, category, http_status):
    server[0][0] = mode
    s = session()
    try:
        events = list(s.stream_events(MESSAGES, TOOLS, None))
    finally:
        s.close()
    assert events[-1].tool_calls
    assert s.diagnostics['native_retries'] == 1
    assert s.diagnostics['native_retry_' + category] == 1
    assert sum(s.diagnostics['native_retry_' + key] for key in ('stream','rate_limit','server','unknown')) == 1
    if http_status:
        assert s.diagnostics['native_retry_http_' + str(http_status)] == 1
    assert all(type(value) is int for value in s.diagnostics.values())
    assert 'PRIVATE' not in repr(s.diagnostics) + repr(events)


def test_public_search_actions_retain_only_completed_whitelisted_fields(server):
    server[0][0] = 'public_actions'
    s = session()
    try:
        list(s.stream_events(MESSAGES, TOOLS, None))
    finally:
        s.close()
    assert s.public_search_actions == [
        {'type':'search','query':('public query '+ 'q'*600)[:512],
         'queries':['repository midi','sheet music']},
        {'type':'openPage','url':'https://example.org/scores/song'},
        {'type':'findInPage'}, {'type':'openPage'}, {'type':'openPage'},
    ]
    assert 'PRIVATE' not in repr(s.public_search_actions)
    assert all(type(value) is int for value in s.diagnostics.values())


def test_public_search_action_history_and_queries_are_bounded(server):
    server[0][0] = 'many_actions'
    s = session()
    try:
        list(s.stream_events(MESSAGES, TOOLS, None))
    finally:
        s.close()
    assert len(s.public_search_actions) == 40
    assert all(len(action['queries']) == 10 for action in s.public_search_actions)
    assert all(len(query) == 512 for action in s.public_search_actions for query in action['queries'])


def test_public_search_urls_exclude_local_and_preserve_public_address_without_query(server):
    server[0][0] = 'public_urls'
    s = session()
    try:
        list(s.stream_events(MESSAGES, TOOLS, None))
    finally:
        s.close()
    assert s.public_search_actions == [*({'type':'openPage'} for _ in range(7)),
        {'type':'openPage','url':'https://example.org/public'},
        {'type':'openPage','url':'http://8.8.8.8/public'},
        {'type':'openPage','url':'https://[2606:4700:4700::1111]/public'}]


@pytest.mark.parametrize('mode,code', [('unknown', 'native_denied_request'),
    ('arguments', 'native_denied_request'), ('foreign', 'native_denied_request'),
    ('user_input', 'native_denied_request'),
    ('terminal_error', 'native_protocol'),
    ('bad_tool', 'native_protocol'), ('bad_id', 'native_protocol'),
    ('bad_params', 'native_protocol'), ('bad_item', 'native_protocol'),
    ('bad_thread', 'native_protocol'), ('bad_turn', 'native_protocol'),
    ('bad_method', 'native_protocol'), ('bad_completed', 'native_protocol'),
    ('bad_thread_id', 'native_protocol'),
    ('approval', 'native_denied_request'), ('eof', 'native_unexpected_eof'),
    ('bytes', 'native_output_limit'), ('large_text', 'native_output_limit'), ('timeout', 'timeout')])
def test_protocol_failure_reaps_owned_process(server, mode, code):
    modes, processes, _, trace = server
    modes[0] = mode
    s = session(timeout_seconds=.4 if mode == 'timeout' else 3)
    with pytest.raises(ProviderError) as error:
        list(s.stream_events(MESSAGES, TOOLS, None))
    assert error.value.code == code
    assert not s.complete
    assert processes[0].poll() is not None
    if mode in {'approval', 'unknown'}:
        records = [json.loads(x) for x in trace.read_text().splitlines()]
        denied = records[-1]
        assert denied.get('error') or denied.get('result', {}).get('decision') == 'decline'
    s.close()


def test_timeout_while_native_server_stops_reading_input(server):
    server[0][0] = 'blocked_write'
    s = session(timeout_seconds=.4)
    with pytest.raises(ProviderError) as error:
        list(s.stream_events([{'role':'user','content':'x' * 200_000}], TOOLS, None))
    assert error.value.code == 'timeout'
    assert server[1][0].poll() is not None


def test_retryable_native_error_continues_original_turn(server):
    server[0][0] = 'retry'
    s = session()
    assert list(s.stream_events(MESSAGES, TOOLS, None))[-1].tool_calls
    list(s.stream_events(feedback('1'), TOOLS, None))
    final = list(s.stream_events(feedback('2'), TOOLS, None))
    assert s.complete and s.diagnostics['turn_starts'] == 1
    assert s.diagnostics['native_retries'] == 1
    assert 'PRIVATE' not in repr(final) + repr(s.diagnostics)


def test_mismatched_gateway_result_closes_pending_server(server):
    s = session()
    list(s.stream_events(MESSAGES, TOOLS, None))
    with pytest.raises(ProviderError) as error:
        list(s.stream_events(feedback('wrong-call'), TOOLS, None))
    assert error.value.code == 'native_tool_mismatch'
    assert server[1][0].poll() is not None


def test_cancel_blocked_native_read(server):
    server[0][0] = 'timeout'
    started = __import__('time').monotonic()
    s = session(cancelled=lambda: __import__('time').monotonic() - started > .3)
    with pytest.raises(ProviderError) as error:
        list(s.stream_events(MESSAGES, TOOLS, None))
    assert error.value.code == 'cancelled'
    assert server[1][0].poll() is not None


def test_explicit_close_reaps_server_waiting_for_gateway(server):
    s = session()
    list(s.stream_events(MESSAGES, TOOLS, None))
    assert server[1][0].poll() is None
    s.close()
    s.close()
    assert server[1][0].poll() is not None


def test_abandoned_generator_reaps_before_gateway_result(server):
    s = session()
    stream = s.stream_events(MESSAGES, TOOLS, None)
    assert next(stream).tool_calls
    stream.close()
    assert server[1][0].poll() is not None


@pytest.mark.skipif(os.name != 'nt', reason='Windows owned process Job Object')
@pytest.mark.parametrize('mode', ['orphan_helper', 'live_helper'])
@pytest.mark.parametrize('early_accounting', [False, True])
def test_windows_close_reaps_helper_after_root_exit_without_blocking(server, mode, early_accounting):
    import ctypes
    from ctypes import wintypes
    server[0][0] = mode
    s = session()
    list(s.stream_events(MESSAGES, TOOLS, None))
    helper_pid = int(__import__('pathlib').Path(str(server[3]) + '.helper').read_text())
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000 | 0x1, False, helper_pid)
    assert handle
    if mode == 'orphan_helper':
        server[1][0].wait(timeout=2)
    delayed_termination = None
    if early_accounting:
        # Deterministically model the observed kernel race: accounting reports
        # zero while a retained process handle has not signaled termination.
        # Keep a second job handle so closing the first cannot hide the race.
        original = s._job._kernel
        original.GetCurrentProcess.restype = wintypes.HANDLE
        original.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
            wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
            wintypes.BOOL, wintypes.DWORD]
        duplicate = wintypes.HANDLE()
        owner = original.GetCurrentProcess()
        assert original.DuplicateHandle(owner, s._job._handle, owner,
            ctypes.byref(duplicate), 0, False, 2)
        class EarlyAccounting:
            def __getattr__(self, name):
                return getattr(original, name)
            def TerminateJobObject(self, job, code):
                nonlocal delayed_termination
                def terminate():
                    time.sleep(.1)
                    original.TerminateJobObject(duplicate, code)
                    original.CloseHandle(duplicate)
                delayed_termination = threading.Thread(target=terminate, daemon=True)
                delayed_termination.start()
                return 1
            def QueryInformationJobObject(self, job, info, data, size, returned):
                if info == 1:
                    ctypes.memset(data, 0, size)
                    return 1
                return original.QueryInformationJobObject(job, info, data, size, returned)
        s._job._kernel = EarlyAccounting()
    finished = threading.Event()
    def close():
        try: s.close()
        finally: finished.set()
    closer = threading.Thread(target=close, daemon=True)
    started = time.monotonic()
    closer.start()
    bounded = finished.wait(2)
    helper_gone = kernel.WaitForSingleObject(handle, 0) == 0
    # Test-owned fallback prevents the deliberately broken implementation from
    # leaving an orphan or blocking the pytest process when the assertion fails.
    if not helper_gone:
        kernel.TerminateProcess(handle, 1)
    kernel.CloseHandle(handle)
    closer.join(timeout=2)
    if delayed_termination:
        delayed_termination.join(timeout=2)
    assert bounded, 'close blocked on inherited pipe reader'
    assert helper_gone, 'owned helper survived close'
    assert time.monotonic() - started < 2
    assert s.diagnostics['cleanup_errors'] == 0

@pytest.mark.parametrize('mode', ['normal', 'approval', 'successful_mcp'])
def test_creator_native_uses_real_workspace_and_inherits_host_tools(server, tmp_path, mode):
    server[0][0] = mode
    workspace = tmp_path / 'real-project'
    workspace.mkdir()
    s = session(creator_workspace=workspace)
    try:
        assert list(s.stream_events(MESSAGES, TOOLS, None))[-1].tool_calls[0].call_id == '1'
        assert s._deadline == 0
        assert s.capabilities['host_mcp']
        assert list(s.stream_events(feedback('1'), TOOLS, None))[-1].tool_calls[0].call_id == '2'
        assert ''.join(e.text_delta for e in s.stream_events(feedback('2'), TOOLS, None)) == 'Draft ready'
        records = [json.loads(x) for x in server[3].read_text().splitlines()]
        start = next(m['params'] for m in records if m.get('method') == 'thread/start')
        assert start['cwd'] == str(workspace.resolve())
        assert start['sandbox'] == 'danger-full-access'
        assert start['config'] == {'web_search': 'live'}
        assert workspace.is_dir()
        assert 'mcp_servers={}' not in server[2][0]
        if mode == 'approval':
            assert next(m for m in records if m.get('id') == 90)['result']['decision'] == 'accept'
    finally:
        s.close()


def test_creator_native_question_returns_real_user_answer(server, tmp_path):
    server[0][0] = 'user_input'
    asked = []
    def answer(payload):
        asked.append(payload)
        return {'answers': {'mood': {'answers': ['bright']}}}
    s = session(creator_workspace=tmp_path, request_user_input=answer)
    try:
        assert list(s.stream_events(MESSAGES, TOOLS, None))[-1].tool_calls[0].call_id == '1'
        assert asked[0]['questions'][0]['question'] == 'Mood?'
        assert s.diagnostics['denied_requests'] == 0
        records = [json.loads(x) for x in server[3].read_text().splitlines()]
        assert next(m for m in records if m.get('id') == 90)['result']['answers']['mood']['answers'] == ['bright']
    finally:
        s.close()
