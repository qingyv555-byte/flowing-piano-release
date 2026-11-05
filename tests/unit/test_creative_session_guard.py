from ai_piano_studio.creative.session_guard import should_pause


def test_lock_disconnect_suspend_pause_but_unlock_does_not_resume():
    assert should_pause(0x02B1, 7)  # WTS_SESSION_LOCK
    assert should_pause(0x02B1, 2)  # console disconnect
    assert should_pause(0x0218, 4)  # suspend
    assert not should_pause(0x02B1, 8)  # unlock never autoplays
    assert not should_pause(0x0218, 18)  # resume never autoplays
    assert not should_pause(0x0200, 0)
