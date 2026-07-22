"""Fail-closed orchestration for evidence-bound structured answers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from study_agent.modules.answering.citation_validator import (
    AnswerPayloadError,
    CitationValidationError,
    CitationValidator,
)
from study_agent.modules.answering.evidence_gate import EvidenceGate
from study_agent.modules.answering.prompts import build_evidence_prompt
from study_agent.modules.answering.types import AnswerExecution, AuthorizedEvidence
from study_agent.providers.errors import ProviderError, ProviderErrorCode
from study_agent.providers.protocols import ChatProvider, ConversationContextTurn
from study_contracts import AnswerStatus, Refusal, StructuredAnswer

type ProviderFactory = Callable[[], ChatProvider]
type SourceStateCheck = Callable[[], Awaitable[bool]]


def _abstain(query_id: str, code: str, message: str) -> StructuredAnswer:
    return StructuredAnswer(
        query_id=query_id,
        status=AnswerStatus.ABSTAINED,
        answer_markdown="",
        refusal=Refusal(code=code, message=message),
    )


class TrustedAnswerService:
    def __init__(
        self,
        provider_factory: ProviderFactory,
        *,
        evidence_gate: EvidenceGate | None = None,
        citation_validator: CitationValidator | None = None,
        timeout_seconds: float = 30.0,
        max_validation_attempts: int = 2,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("answer timeout must be positive")
        if max_validation_attempts <= 0:
            raise ValueError("validation attempts must be positive")
        self._provider_factory = provider_factory
        self._evidence_gate = evidence_gate or EvidenceGate()
        self._citation_validator = citation_validator or CitationValidator()
        self._timeout_seconds = timeout_seconds
        self._max_validation_attempts = max_validation_attempts

    async def answer(
        self,
        *,
        query_id: str,
        question: str,
        active_index: bool,
        candidates: tuple[AuthorizedEvidence, ...],
        sources_are_current: SourceStateCheck,
        conversation_context: tuple[ConversationContextTurn, ...] = (),
    ) -> AnswerExecution:
        decision = self._evidence_gate.evaluate(
            active_index=active_index,
            candidates=candidates,
        )
        if not decision.sufficient:
            return AnswerExecution(answer=_abstain(query_id, decision.code.value, decision.message))

        try:
            provider = self._provider_factory()
        except ProviderError as exc:
            return self._provider_failure(exc)

        prompt = build_evidence_prompt(
            question,
            decision.candidates,
            conversation_context=conversation_context,
        )
        last_draft_model: str | None = None
        for _attempt in range(self._max_validation_attempts):
            try:
                draft = await asyncio.wait_for(
                    provider.answer(prompt),
                    timeout=self._timeout_seconds,
                )
                last_draft_model = draft.model
                answer = self._citation_validator.validate(
                    query_id=query_id,
                    payload=draft.payload,
                    authorized=decision.candidates,
                )
            except TimeoutError:
                return AnswerExecution(answer=None, failure_code=ProviderErrorCode.TIMEOUT.value)
            except ProviderError as exc:
                return self._provider_failure(exc)
            except AnswerPayloadError:
                return AnswerExecution(
                    answer=None,
                    failure_code=ProviderErrorCode.BAD_RESPONSE.value,
                    model=last_draft_model,
                )
            except CitationValidationError:
                continue

            if answer.status is AnswerStatus.ANSWERED:
                try:
                    current = await sources_are_current()
                except Exception:
                    current = False
                if not current:
                    return AnswerExecution(
                        answer=_abstain(
                            query_id,
                            "SOURCE_CHANGED",
                            "回答生成期间资料版本或删除状态发生变化, 请重试。",
                        ),
                        model=draft.model,
                        provider_response_id=draft.provider_response_id,
                        usage=dict(draft.usage),
                    )
            return AnswerExecution(
                answer=answer,
                model=draft.model,
                provider_response_id=draft.provider_response_id,
                usage=dict(draft.usage),
            )

        return AnswerExecution(
            answer=_abstain(
                query_id,
                "INVALID_CITATION",
                "模型未能生成可由本次课件证据验证的引用。",
            ),
            model=last_draft_model,
        )

    @staticmethod
    def _provider_failure(exc: ProviderError) -> AnswerExecution:
        return AnswerExecution(
            answer=None,
            failure_code=exc.code.value,
            provider=exc.provider,
        )
