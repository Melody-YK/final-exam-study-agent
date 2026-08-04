from study_agent.modules.answering.memory import (
    LearnerMemoryType,
    extract_explicit_memories,
)


def test_extracts_only_explicit_preferences_goals_and_confirmed_misconceptions() -> None:
    candidates = extract_explicit_memories(
        "我喜欢先看例子。我的目标是掌握进程调度。"
        "我总是把进程和线程混淆。系统可能觉得我不擅长操作系统。"
    )

    assert [(candidate.memory_type, candidate.content) for candidate in candidates] == [
        (LearnerMemoryType.PREFERENCE, "我喜欢先看例子"),
        (LearnerMemoryType.LEARNING_GOAL, "我的目标是掌握进程调度"),
        (LearnerMemoryType.CONFIRMED_MISCONCEPTION, "我总是把进程和线程混淆"),
    ]


def test_does_not_persist_model_style_inferences_or_ordinary_questions() -> None:
    assert extract_explicit_memories("请解释什么是进程?") == ()
    assert extract_explicit_memories("这个学生可能更适合图示") == ()
