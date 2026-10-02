import json
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.schemas.cards import CritiqueReport
    from app.temporal.activities import (
        critique_activity,
        critique_context_activity,
        empty_context_card_activity,
        extract_activity,
        filter_output_activity,
        generate_activity,
        generate_context_activity,
        index_document_activity,
        parse_document_activity,
        prepare_document_activity,
        search_context_activity,
        set_status_activity,
        verify_citations_activity,
    )

ACTIVITY_TIMEOUT = timedelta(seconds=300)
INDEX_TIMEOUT = timedelta(minutes=30)
EXTRACT_RETRY = RetryPolicy(maximum_attempts=2)
GENERATE_RETRY = RetryPolicy(maximum_attempts=3)
STATUS_RETRY = RetryPolicy(maximum_attempts=3)


@workflow.defn
class CardWorkflow:
    def __init__(self) -> None:
        self._status = "starting"
        self._decision: str | None = None
        self._request_id = ""

    @workflow.run
    async def run(
        self,
        job_id: str,
        supplier_text: str,
        max_attempts: int = 3,
        document_ids: list[str] | None = None,
        product_hint: str = "",
        request_id: str = "",
    ) -> str:
        self._request_id = request_id
        if document_ids:
            return await self._run_documents(job_id, document_ids, product_hint, max_attempts)
        await self._write_status(job_id, "extracting")
        facts_json = await workflow.execute_activity(
            extract_activity,
            args=[supplier_text, job_id, self._request_id],
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=EXTRACT_RETRY,
        )
        feedback: list[str] | None = None
        draft_json = ""
        for _attempt in range(1, max_attempts + 1):
            await self._write_status(job_id, "generating", bump_attempts=True)
            feedback_json = json.dumps(feedback, ensure_ascii=False) if feedback else None
            draft_json = await workflow.execute_activity(
                generate_activity,
                args=[facts_json, feedback_json, job_id, self._request_id],
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=GENERATE_RETRY,
            )
            await self._write_status(job_id, "critiquing")
            report_json = await workflow.execute_activity(
                critique_activity,
                args=[facts_json, draft_json, job_id, self._request_id],
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=GENERATE_RETRY,
            )
            report = CritiqueReport.model_validate_json(report_json)
            if report.verdict == "approve":
                break
            feedback = report.issues
        await self._write_status(job_id, "awaiting_confirmation", result_json=draft_json)
        await workflow.wait_condition(lambda: self._decision is not None)
        final = "approved" if self._decision == "approve" else "rejected"
        await self._write_status(job_id, final, result_json=draft_json)
        return draft_json

    async def _run_documents(
        self,
        job_id: str,
        document_ids: list[str],
        product_hint: str,
        max_attempts: int,
    ) -> str:
        await self._write_status(job_id, "разбор")
        for document_id in document_ids:
            await workflow.execute_activity(
                prepare_document_activity,
                document_id,
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=STATUS_RETRY,
            )
        await self._write_status(job_id, "индексация")
        for document_id in document_ids:
            await workflow.execute_activity(
                index_document_activity,
                document_id,
                start_to_close_timeout=INDEX_TIMEOUT,
                retry_policy=STATUS_RETRY,
            )
        await self._write_status(job_id, "поиск")
        context_json = await workflow.execute_activity(
            search_context_activity,
            json.dumps(
                {
                    "document_ids": list(document_ids),
                    "product_hint": product_hint,
                    "job_id": job_id,
                    "request_id": self._request_id,
                },
                ensure_ascii=False,
            ),
            start_to_close_timeout=INDEX_TIMEOUT,
            retry_policy=EXTRACT_RETRY,
        )
        context = json.loads(context_json)
        security_json = json.dumps(context.get("security") or {}, ensure_ascii=False)
        draft_json = ""
        citation_failures = 0
        if context.get("blocked"):
            draft_json = await workflow.execute_activity(
                empty_context_card_activity,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=STATUS_RETRY,
            )
            draft_json = await workflow.execute_activity(
                filter_output_activity,
                args=[draft_json, security_json, job_id, self._request_id],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=STATUS_RETRY,
            )
            await self._write_status(
                job_id,
                "ожидание",
                result_json=draft_json,
                error=context.get("block_reason") or "подозрительный документ",
            )
        elif not context["ids"]:
            draft_json = await workflow.execute_activity(
                empty_context_card_activity,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=STATUS_RETRY,
            )
            draft_json = await workflow.execute_activity(
                filter_output_activity,
                args=[draft_json, security_json, job_id, self._request_id],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=STATUS_RETRY,
            )
            await self._write_status(
                job_id,
                "ожидание",
                result_json=draft_json,
                error="контекст пуст",
            )
        else:
            feedback: list[str] | None = None
            ids_json = json.dumps(context["ids"], ensure_ascii=False)
            pending_errors: list[str] = []
            for _attempt in range(1, max_attempts + 1):
                await self._write_status(job_id, "генерация", bump_attempts=True)
                feedback_json = json.dumps(feedback, ensure_ascii=False) if feedback else None
                draft_json = await workflow.execute_activity(
                    generate_context_activity,
                    args=[context["text"], feedback_json, ids_json, job_id, self._request_id],
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=GENERATE_RETRY,
                )
                await self._write_status(job_id, "проверка")
                errors_json = await workflow.execute_activity(
                    verify_citations_activity,
                    args=[draft_json, ids_json],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=STATUS_RETRY,
                )
                errors = json.loads(errors_json)
                pending_errors = errors
                if errors:
                    citation_failures += 1
                    if citation_failures >= 2:
                        draft_json = await workflow.execute_activity(
                            filter_output_activity,
                            args=[draft_json, security_json, job_id, self._request_id],
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=STATUS_RETRY,
                        )
                        await self._write_status(
                            job_id,
                            "ожидание",
                            result_json=draft_json,
                            error="\n".join(errors),
                        )
                        break
                    feedback = errors
                    continue
                report_json = await workflow.execute_activity(
                    critique_context_activity,
                    args=[context["text"], draft_json, job_id, self._request_id],
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=GENERATE_RETRY,
                )
                report = CritiqueReport.model_validate_json(report_json)
                if report.verdict == "approve":
                    break
                feedback = report.issues
            if citation_failures < 2:
                draft_json = await workflow.execute_activity(
                    filter_output_activity,
                    args=[draft_json, security_json, job_id, self._request_id],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=STATUS_RETRY,
                )
                # Последняя попытка могла закончиться ошибкой ссылок: человек должен её увидеть.
                await self._write_status(
                    job_id,
                    "ожидание",
                    result_json=draft_json,
                    error="\n".join(pending_errors) if pending_errors else None,
                )
        await workflow.wait_condition(lambda: self._decision is not None)
        final = "согласовано" if self._decision == "approve" else "отказ"
        await self._write_status(job_id, final, result_json=draft_json)
        return draft_json

    async def _write_status(
        self,
        job_id: str,
        status: str,
        *,
        result_json: str | None = None,
        bump_attempts: bool = False,
        error: str | None = None,
    ) -> None:
        self._status = status
        body = {
            "job_id": job_id,
            "request_id": self._request_id,
            "status": status,
            "result_json": result_json,
            "bump_attempts": bump_attempts,
        }
        if error is not None:
            body["error"] = error
        payload = json.dumps(body, ensure_ascii=False)
        await workflow.execute_activity(
            set_status_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=STATUS_RETRY,
        )

    @workflow.signal
    def approve(self) -> None:
        self._decision = "approve"

    @workflow.signal
    def reject(self) -> None:
        self._decision = "reject"

    @workflow.query
    def current_status(self) -> str:
        return self._status


@workflow.defn
class DocumentWorkflow:
    @workflow.run
    async def run(self, document_id: str) -> str:
        await workflow.execute_activity(
            parse_document_activity,
            document_id,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=STATUS_RETRY,
        )
        await workflow.execute_activity(
            index_document_activity,
            document_id,
            start_to_close_timeout=INDEX_TIMEOUT,
            retry_policy=STATUS_RETRY,
        )
        return document_id
