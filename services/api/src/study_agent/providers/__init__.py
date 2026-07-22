"""Vendor-neutral contracts and fail-closed real provider adapters."""

from study_agent.providers.deepseek import DeepSeekChatProvider
from study_agent.providers.embedding_openai import OpenAICompatibleEmbeddingProvider
from study_agent.providers.errors import ProviderError, ProviderErrorCode
from study_agent.providers.factory import ProviderRegistry, build_provider_registry
from study_agent.providers.protocols import (
    ChatProvider,
    Clock,
    ConversationContextTurn,
    EmbeddingContract,
    EmbeddingProvider,
    EvidencePrompt,
    ObjectMetadata,
    ObjectScope,
    ObjectStorage,
    Passage,
    RerankProvider,
    RerankScore,
    SignedUrl,
    StructuredAnswerDraft,
    UploadTarget,
)

__all__ = [
    "ChatProvider",
    "Clock",
    "ConversationContextTurn",
    "DeepSeekChatProvider",
    "EmbeddingContract",
    "EmbeddingProvider",
    "EvidencePrompt",
    "ObjectMetadata",
    "ObjectScope",
    "ObjectStorage",
    "OpenAICompatibleEmbeddingProvider",
    "Passage",
    "ProviderError",
    "ProviderErrorCode",
    "ProviderRegistry",
    "RerankProvider",
    "RerankScore",
    "SignedUrl",
    "StructuredAnswerDraft",
    "UploadTarget",
    "build_provider_registry",
]
