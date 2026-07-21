"""Deterministic Jieba tokenization with a versioned course dictionary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

import jieba  # type: ignore[import-untyped]


class ChineseTokenizer:
    """An isolated Jieba tokenizer whose custom dictionary is hash-addressed."""

    def __init__(self, course_terms: Iterable[str]) -> None:
        self._terms = tuple(sorted({term.strip().lower() for term in course_terms if term.strip()}))
        self._tokenizer = jieba.Tokenizer()
        for term in self._terms:
            self._tokenizer.add_word(term, freq=2_000_000_000)
        encoded = json.dumps(
            self._terms,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.dictionary_hash = hashlib.sha256(encoded).hexdigest()
        self.version = f"jieba-{jieba.__version__}:{self.dictionary_hash[:12]}"

    @property
    def course_terms(self) -> tuple[str, ...]:
        return self._terms

    def tokenize(self, text: str) -> list[str]:
        return [
            token
            for piece in self._tokenizer.lcut(text, HMM=False)
            if (token := piece.strip().lower())
        ]
