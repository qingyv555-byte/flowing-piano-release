from ai_piano_studio.creative.audio_lease import PlaybackArbiter


def test_studio_busy_blocks_background_without_stopping_studio():
    events = []
    busy = [True]
    broker = PlaybackArbiter(lambda: busy[0], lambda owner, notes: events.append(('start', owner, notes)), lambda: events.append(('release-own',)))
    assert broker.play('companion', (1,)) is None
    assert not events
    busy[0] = False
    lease = broker.play('companion', (1,))
    assert lease is not None
    busy[0] = True
    broker.check_studio()
    assert events == [('start', 'companion', (1,)), ('release-own',)]
    assert not broker.is_current(lease)
    assert broker.owner == ''


def test_synchronous_failed_callback_invalidates_returned_lease():
    broker = PlaybackArbiter(lambda: False, lambda *_: broker.release(), lambda: None)
    assert broker.play('preview', (1,)) is None


def test_explicit_preview_preempts_companion_but_stale_release_cannot_stop_new_owner():
    events = []
    broker = PlaybackArbiter(lambda: False, lambda owner, notes: events.append(owner), lambda: events.append('release-own'))
    companion = broker.play('companion', (1,))
    preview = broker.play('preview', (2,))
    assert events == ['companion', 'release-own', 'preview']
    broker.release(companion)
    assert broker.is_current(preview)
    assert broker.play('companion', (3,)) is None
    broker.release(preview)
    assert broker.owner == ''


def test_failed_start_does_not_leave_a_valid_lease():
    def fail(*args):
        raise RuntimeError('device unavailable')
    broker = PlaybackArbiter(lambda: False, fail, lambda: None)
    import pytest
    with pytest.raises(RuntimeError):
        broker.play('preview', (1,))
    assert broker.owner == ''
