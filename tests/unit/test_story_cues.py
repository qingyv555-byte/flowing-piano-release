import pytest
from ai_piano_studio.creative.story import StorySession, CuePlan
from ai_piano_studio.domain.note import Note
from ai_piano_studio.audio.synth import render_project


def test_story_segments_require_explicit_selection_and_clear():
    story = StorySession('role-a')
    story.prepare('第一段。\n\n第二段。')
    assert story.selected is None
    assert story.select(1) == '第二段。'
    assert story.selected == 1
    story.clear()
    assert not story.segments and story.selected is None
    with pytest.raises(ValueError):
        story.prepare('x' * 24001)


def test_cues_shift_entry_and_keep_rest_silent_in_offline_pcm():
    notes = (Note.create(pitch=60, start_time=0, duration=2),
             Note.create(pitch=64, start_time=2.2, duration=.5))
    plan = CuePlan(5, 1, ((2, 3),), (3.2,))
    result = plan.apply(notes)
    assert result[0].start_time == 1
    assert result[-1].pitch == 64
    assert result[-1].velocity > notes[-1].velocity
    pcm = render_project(result, sample_rate=8000)
    assert not any(pcm[:8000])
    assert not any(pcm[16000:24000])
    assert any(pcm[25600:28000])


@pytest.mark.parametrize('rests', [((2, 4), (3, 5)), ((-1, 2),), ((4, 6),), ((2, 2),)])
def test_cue_plan_rejects_overlap_and_invalid_ranges(rests):
    with pytest.raises(ValueError):
        CuePlan(5, 0, rests)
