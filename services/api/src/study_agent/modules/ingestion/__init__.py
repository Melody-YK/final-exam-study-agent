"""Validated parse-result ingestion and deterministic chunk derivation."""

from study_agent.modules.ingestion.chunker import chunk_pages
from study_agent.modules.ingestion.revisions import IngestionOutcome, RevisionService

__all__ = ["IngestionOutcome", "RevisionService", "chunk_pages"]
