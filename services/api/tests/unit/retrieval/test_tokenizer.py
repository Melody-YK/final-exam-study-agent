from study_agent.modules.retrieval.tokenizer import ChineseTokenizer


def test_course_term_is_kept_as_one_token_and_dictionary_hash_is_stable() -> None:
    term = "页式虚拟存储管理"
    first = ChineseTokenizer(course_terms=[term, " 缺页中断 "])
    second = ChineseTokenizer(course_terms=["缺页中断", term, term])

    assert term in first.tokenize(f"{term}会触发缺页中断")
    assert first.dictionary_hash == second.dictionary_hash
    assert first.version.startswith("jieba-")


def test_tokenizer_discards_whitespace_and_normalizes_ascii_case() -> None:
    tokenizer = ChineseTokenizer(course_terms=[])

    tokens = tokenizer.tokenize("  CPU 调度\nCPU  ")

    assert "cpu" in tokens
    assert all(token.strip() == token and token for token in tokens)
