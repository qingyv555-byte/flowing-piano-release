from ai_piano_studio.creative.audio_lease import PlaybackArbiter
from ai_piano_studio.creative.companion import CompanionScheduler
from ai_piano_studio.domain.note import Note


def make_scheduler():
    events, busy = [], [False]
    arbiter = PlaybackArbiter(lambda: busy[0], lambda owner, notes: events.append(notes), lambda: events.append('release'))
    scheduler = CompanionScheduler(arbiter)
    notes = tuple(Note.create(pitch=60 + i, start_time=i * .5, duration=.3) for i in range(8))
    return scheduler, arbiter, events, busy, notes


def test_phrase_boundary_and_bounded_activity_preserve_theme():
    scheduler, _, events, _, notes = make_scheduler()
    scheduler.start(notes, now=0)
    scheduler.tick(0)
    assert len(events) == 1
    for _ in range(10000):
        scheduler.activity(movement=100, wheel=100)
    scheduler.tick(.25)
    assert len(events) == 1
    scheduler.tick(4)
    variant = events[-1]
    assert 0 < len(variant) < len(notes)
    assert {n.pitch for n in variant} <= {n.pitch for n in notes}
    assert variant[0].pitch == notes[0].pitch
    assert max(n.velocity for n in variant) <= 64


def test_pause_clears_activity_and_cannot_release_foreign_preview():
    scheduler, arbiter, events, _, notes = make_scheduler()
    scheduler.start(notes, now=0)
    scheduler.tick(0)
    preview = arbiter.play('preview', notes)
    scheduler.pause()
    before = len(events)
    scheduler.activity(movement=100, wheel=100)
    scheduler.tick(100)
    assert len(events) == before
    assert arbiter.is_current(preview)
    assert scheduler.state == 'paused'


def test_studio_preemption_and_clock_jump_never_build_a_queue():
    scheduler, _, events, busy, notes = make_scheduler()
    scheduler.start(notes, now=0)
    scheduler.tick(0)
    busy[0] = True
    scheduler.tick(.25)
    assert scheduler.state == 'yielding'
    assert events[-1] == 'release'
    busy[0] = False
    before = len(events)
    scheduler.tick(1000)
    assert len(events) == before + 1
    scheduler.pause()
    scheduler.tick(2000)
    assert scheduler.state == 'paused'


def test_same_theme_and_activity_reproduce_same_variation():
    first, _, a, _, notes = make_scheduler()
    second, _, b, _, _ = make_scheduler()
    for scheduler in (first, second):
        scheduler.start(notes, now=0)
        scheduler.activity(movement=200)
        scheduler.tick(0)
    assert a == b
