from study_agent.modules.courses.manifest import CorpusRole, ManifestEntry, ManifestPolicy


def test_only_corpus_role_is_indexable() -> None:
    policy = ManifestPolicy()

    assert policy.is_indexable(CorpusRole.CORPUS) is True
    assert policy.is_indexable(CorpusRole.QUESTIONS) is False
    assert policy.is_indexable(CorpusRole.GOLD_ANSWERS) is False
    assert policy.is_indexable(CorpusRole.OCR_GOLD) is False
    assert policy.is_indexable(CorpusRole.EXCLUDED) is False


def test_deduplication_key_keeps_roles_isolated() -> None:
    corpus = ManifestEntry(
        course_id="course-1",
        filename="chapter.pdf",
        sha256="a" * 64,
        role=CorpusRole.CORPUS,
    )
    answer = corpus.model_copy(update={"role": CorpusRole.GOLD_ANSWERS})

    assert corpus.deduplication_key != answer.deduplication_key
