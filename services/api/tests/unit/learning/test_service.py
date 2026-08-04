from study_agent.modules.learning.service import _generation_target_schedule


def test_generation_target_schedule_covers_each_exercise_prototype_once_by_default() -> None:
    assert _generation_target_schedule((7,), 7) == tuple((0, index) for index in range(7))


def test_generation_target_schedule_preserves_each_scope_and_spreads_prototypes() -> None:
    assert _generation_target_schedule((7, 1), 8) == (
        (0, 0),
        (1, 0),
        (0, 1),
        (0, 2),
        (0, 3),
        (0, 4),
        (0, 5),
        (0, 6),
    )
    assert _generation_target_schedule((7, 1), 5) == (
        (0, 0),
        (1, 0),
        (0, 2),
        (0, 4),
        (0, 6),
    )
    assert _generation_target_schedule((1, 1), 5) == (
        (0, 0),
        (1, 0),
        (0, 0),
        (1, 0),
        (0, 0),
    )
