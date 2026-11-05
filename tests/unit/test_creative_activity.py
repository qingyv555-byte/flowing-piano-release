from ai_piano_studio.creative.activity import ActivityAccumulator


def test_activity_retains_only_bounded_magnitudes_and_clears_on_take():
    activity = ActivityAccumulator()
    for _ in range(10000):
        activity.add(30, 1)
    assert activity.take() == (1000, 100)
    assert activity.take() == (0, 0)
    activity.add(5, 0)
    activity.clear()
    assert activity.take() == (0, 0)
