import json
from pathlib import Path

import pytest

from ai_piano_studio.score_lab.cli_transcriber import owned_cli, transcribe
from ai_piano_studio.score_lab.contracts import LabFailure
from test_contracts import output


def image(tmp_path):
    path = tmp_path/'actual.png'
    path.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    return path


def fake_runner(data, *, terminal=True):
    def run(path,args,**kwargs):
        value = dict(readable=True,reason='Injected readability output; no accuracy claim') if 'readability-schema.json' in args[args.index('--output-schema')+1] else data
        Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(value),encoding='utf-8')
        if terminal:
            kwargs['on_event'](dict(type='turn.completed'))
    return run


def test_real_initial_image_schema_and_disabled_tools(tmp_path):
    png = image(tmp_path)
    seen = []
    def runner(path,args,**kwargs):
        seen.append(args)
        assert args[args.index('--image')+1] == str(png.resolve())
        assert args[args.index('--sandbox')+1] == 'read-only'
        assert all(x in args for x in ['--ignore-user-config','--ignore-rules','--output-schema','features.shell_tool=false','features.apps=false','web_search="disabled"'])
        assert kwargs['timeout'] == 240
        assert '1/64 quarter beat' in kwargs['text']
        assert 'before the target note' in kwargs['text']
        assert 'Chinese uncertainty' in kwargs['text']
        kwargs['on_event'](dict(type='item.completed',item=dict(type='reasoning',text='private reasoning')))
        fake_runner(output())(path,args,**kwargs)
    assert transcribe([png],tmp_path/'job','selected-model',15,runner=runner)['bpm'] == 120
    assert len(seen) == 1
    assert all('private reasoning' not in p.read_text() for p in (tmp_path/'job').glob('*.json'))


def test_initial_original_image_has_bounded_total_size(tmp_path):
    png = image(tmp_path)
    png.write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*8_000_000)
    with pytest.raises(LabFailure) as error:
        transcribe([png],tmp_path/'job','model',15,runner=lambda *a,**k:pytest.fail('Oversized image reached CLI'))
    assert error.value.code == 'images'


def test_only_one_format_repair_and_same_images(tmp_path):
    png = image(tmp_path)
    seen = []
    def run(path,args,**kwargs):
        seen.append((args,kwargs['text']))
        fake_runner({})(path,args,**kwargs)
    with pytest.raises(LabFailure) as error:
        transcribe([png],tmp_path/'job','model',15,runner=run)
    assert error.value.code == 'format' and len(seen) == 2
    assert [x for x in seen[0][0] if x == str(png)] == [str(png)]
    assert 'One format repair' in seen[1][1]


def test_exit_zero_without_success_terminal_is_failure(tmp_path):
    with pytest.raises(LabFailure) as error:
        transcribe([image(tmp_path)],tmp_path/'job','model',15,runner=fake_runner(output(),terminal=False))
    assert error.value.code == 'cli_terminal'


def test_unreadable_stops_without_repair(tmp_path):
    calls = []
    def run(path,args,**kwargs):
        calls.append(1)
        fake_runner(dict(output(),can_read=False))(path,args,**kwargs)
    with pytest.raises(LabFailure) as error:
        transcribe([image(tmp_path)],tmp_path/'job','model',15,runner=run)
    assert error.value.code == 'unreadable' and len(calls) == 1


@pytest.mark.parametrize('cancelled,timeout,code',[(lambda:True,10,'cancelled'),(lambda:False,.02,'timeout')])
def test_owned_process_cancel_and_timeout_reaped(tmp_path,cancelled,timeout,code,monkeypatch):
    import sys
    import subprocess
    processes = []
    original = subprocess.Popen
    def spawn(*args,**kwargs):
        process = original(*args,**kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(subprocess,'Popen',spawn)
    with pytest.raises(LabFailure) as error:
        owned_cli(sys.executable,['-c','import time; time.sleep(60)'],text='',cwd=str(tmp_path),
                  timeout=timeout,cancelled=cancelled,on_event=lambda _:None,on_progress=lambda _:None)
    record = json.loads((tmp_path/'process.json').read_text())
    assert error.value.code == code
    assert record['status'] == 'exited'
    assert processes[0].pid == record['pid']
    assert processes[0].poll() is not None
