"""Corpus manifest roles and isolation policy."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator


class CorpusRole(StrEnum):
    CORPUS = "corpus"
    QUESTIONS = "questions"
    GOLD_ANSWERS = "gold_answers"
    OCR_GOLD = "ocr_gold"
    EXCLUDED = "excluded"


class ManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    course_id: str
    filename: str
    sha256: str
    role: CorpusRole

    @field_validator("course_id", "filename")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("value must not be empty")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.lower()
        is_hexadecimal = all(character in "0123456789abcdef" for character in normalized)
        if len(normalized) != 64 or not is_hexadecimal:
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        return normalized

    @property
    def deduplication_key(self) -> tuple[str, str, str]:
        return self.course_id, self.role.value, self.sha256


class ManifestPolicy:
    @staticmethod
    def is_indexable(role: CorpusRole) -> bool:
        return role is CorpusRole.CORPUS
