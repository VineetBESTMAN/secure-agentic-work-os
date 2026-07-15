import math
import re
import time
from datetime import datetime, timezone
from statistics import mean
from uuid import uuid4

from app.core.config import get_settings
from app.core.database import decode_json, encode_json, get_connection
from app.models.schemas import (
    RagEvaluationCaseRecord,
    RagEvaluationCitation,
    RagEvaluationComparison,
    RagEvaluationDatasetCreate,
    RagEvaluationDatasetRecord,
    RagEvaluationResultRecord,
    RagEvaluationRunRecord,
)
from app.services.embeddings import embedding_service
from app.services.observability import observability_service

EVIDENCE_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{1,}")
EVIDENCE_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}


def _as_string(value) -> str | None:
    return str(value) if value is not None else None


def _average(values: list[float]) -> float:
    return round(mean(values), 3) if values else 0.0


def _percent(value: float) -> float:
    return round(value * 100, 2)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return round(ordered[index], 3)


def _excerpt(text: str, limit: int = 420) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0] + "..."


def _fact_supported(fact: str, evidence: str) -> bool:
    fact_tokens = {
        token
        for token in EVIDENCE_TOKEN_PATTERN.findall(fact.lower())
        if token not in EVIDENCE_STOP_WORDS
    }
    if not fact_tokens:
        return False
    evidence_tokens = set(EVIDENCE_TOKEN_PATTERN.findall(evidence.lower()))
    return len(fact_tokens & evidence_tokens) / len(fact_tokens) >= 0.75


class RagEvaluationService:
    def create_dataset(
        self,
        payload: RagEvaluationDatasetCreate,
        created_by: str,
        role: str,
        organization_id: str = "org_default",
    ) -> RagEvaluationDatasetRecord:
        corpus = self._load_corpus(
            document_ids=payload.document_ids,
            role=role,
            organization_id=organization_id,
        )
        corpus_document_ids = {row["document_id"] for row in corpus}
        corpus_chunk_ids = {row["chunk_id"] for row in corpus}
        if not corpus:
            raise ValueError("The evaluation corpus has no accessible, safe document chunks.")
        if set(payload.document_ids) - corpus_document_ids:
            raise ValueError(
                "Every selected corpus document must exist, be accessible, be safe, and "
                "contain at least one searchable chunk."
            )

        for case in payload.cases:
            missing_documents = set(case.expected_document_ids) - corpus_document_ids
            missing_chunks = set(case.expected_chunk_ids) - corpus_chunk_ids
            if missing_documents or missing_chunks:
                raise ValueError(
                    "Evaluation expectations must reference accessible chunks in the "
                    "selected corpus."
                )

        dataset_id = f"evalds_{uuid4().hex}"
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO rag_evaluation_datasets (
                    dataset_id, name, description, document_ids_json, top_k,
                    minimum_score, created_by, organization_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_id,
                    payload.name.strip(),
                    payload.description.strip(),
                    encode_json(list(dict.fromkeys(payload.document_ids))),
                    payload.top_k,
                    payload.minimum_score,
                    created_by,
                    organization_id,
                ),
            )
            connection.executemany(
                """
                INSERT INTO rag_evaluation_cases (
                    case_id, dataset_id, position, question,
                    expected_document_ids_json, expected_chunk_ids_json,
                    expected_facts_json, reference_answer, unanswerable,
                    organization_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        f"evalcase_{uuid4().hex}",
                        dataset_id,
                        position,
                        case.question.strip(),
                        encode_json(list(dict.fromkeys(case.expected_document_ids))),
                        encode_json(list(dict.fromkeys(case.expected_chunk_ids))),
                        encode_json([fact.strip() for fact in case.expected_facts]),
                        case.reference_answer.strip(),
                        case.unanswerable,
                        organization_id,
                    )
                    for position, case in enumerate(payload.cases)
                ],
            )
        return self.get_dataset(dataset_id, organization_id=organization_id)

    def list_datasets(
        self,
        role: str,
        actor_id: str,
        organization_id: str = "org_default",
    ) -> list[RagEvaluationDatasetRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT d.*, COUNT(c.case_id) AS case_count
                FROM rag_evaluation_datasets d
                LEFT JOIN rag_evaluation_cases c ON c.dataset_id = d.dataset_id
                WHERE d.organization_id = ?
                GROUP BY d.dataset_id
                ORDER BY d.created_at DESC
                """,
                (organization_id,),
            ).fetchall()
        datasets = [self._row_to_dataset(row, cases=[]) for row in rows]
        return [
            dataset
            for dataset in datasets
            if self._dataset_accessible(
                dataset,
                role=role,
                actor_id=actor_id,
                organization_id=organization_id,
            )
        ]

    def get_dataset(
        self,
        dataset_id: str,
        role: str | None = None,
        actor_id: str | None = None,
        organization_id: str = "org_default",
    ) -> RagEvaluationDatasetRecord:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT d.*, COUNT(c.case_id) AS case_count
                FROM rag_evaluation_datasets d
                LEFT JOIN rag_evaluation_cases c ON c.dataset_id = d.dataset_id
                WHERE d.dataset_id = ? AND d.organization_id = ?
                GROUP BY d.dataset_id
                """,
                (dataset_id, organization_id),
            ).fetchone()
            case_rows = connection.execute(
                """
                SELECT * FROM rag_evaluation_cases
                WHERE dataset_id = ? AND organization_id = ?
                ORDER BY position ASC
                """,
                (dataset_id, organization_id),
            ).fetchall()
        if row is None:
            raise ValueError("RAG evaluation dataset not found.")
        cases = [self._row_to_case(case_row) for case_row in case_rows]
        dataset = self._row_to_dataset(row, cases=cases)
        if role is not None and not self._dataset_accessible(
            dataset,
            role=role,
            actor_id=actor_id,
            organization_id=organization_id,
        ):
            raise ValueError("RAG evaluation dataset not found.")
        return dataset

    def run_dataset(
        self,
        dataset_id: str,
        providers: list[str],
        created_by: str,
        role: str,
        organization_id: str = "org_default",
    ) -> RagEvaluationComparison:
        dataset = self.get_dataset(
            dataset_id,
            role=role,
            actor_id=created_by,
            organization_id=organization_id,
        )
        corpus = self._load_corpus(
            document_ids=dataset.document_ids,
            role=role,
            organization_id=organization_id,
        )
        if not corpus:
            raise ValueError("The evaluation corpus has no accessible, safe document chunks.")
        max_chunks = get_settings().rag_evaluation_max_chunks
        if len(corpus) > max_chunks:
            raise ValueError(
                f"The corpus has {len(corpus)} chunks; the evaluation limit is {max_chunks}. "
                "Create a dataset with an explicit document_ids corpus."
            )

        comparison_id = f"evalcmp_{uuid4().hex}"
        runs: list[RagEvaluationRunRecord] = []
        for provider in providers:
            run_id = self._create_run(
                comparison_id=comparison_id,
                dataset=dataset,
                provider=provider,
                created_by=created_by,
                organization_id=organization_id,
            )
            unavailable_reason = embedding_service.provider_unavailable_reason(provider)
            if unavailable_reason:
                self._finish_without_results(
                    run_id=run_id,
                    status="skipped",
                    error=unavailable_reason,
                )
            else:
                try:
                    with observability_service.context(
                        created_by, organization_id=organization_id
                    ):
                        self._execute_run(
                            run_id=run_id,
                            dataset=dataset,
                            corpus=corpus,
                            provider=provider,
                            organization_id=organization_id,
                        )
                except Exception as exc:
                    self._finish_without_results(
                        run_id=run_id,
                        status="failed",
                        error=str(exc),
                    )
            runs.append(
                self.get_run(
                    run_id,
                    role=role,
                    actor_id=created_by,
                    organization_id=organization_id,
                )
            )
        return RagEvaluationComparison(
            comparison_id=comparison_id,
            dataset_id=dataset_id,
            runs=runs,
        )

    def list_runs(
        self,
        role: str,
        actor_id: str,
        limit: int = 50,
        organization_id: str = "org_default",
    ) -> list[RagEvaluationRunRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT r.*, d.name AS dataset_name
                FROM rag_evaluation_runs r
                JOIN rag_evaluation_datasets d ON d.dataset_id = r.dataset_id
                WHERE r.organization_id = ?
                ORDER BY r.created_at DESC
                LIMIT ?
                """,
                (organization_id, limit),
            ).fetchall()
        runs = [self._row_to_run(row, results=[]) for row in rows]
        accessible_dataset_ids = {
            dataset.dataset_id
            for dataset in self.list_datasets(
                role=role,
                actor_id=actor_id,
                organization_id=organization_id,
            )
        }
        return [
            run
            for run in runs
            if run.dataset_id in accessible_dataset_ids
            and (role == "admin" or run.created_by == actor_id)
        ]

    def get_run(
        self,
        run_id: str,
        role: str | None = None,
        actor_id: str | None = None,
        organization_id: str = "org_default",
    ) -> RagEvaluationRunRecord:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT r.*, d.name AS dataset_name
                FROM rag_evaluation_runs r
                JOIN rag_evaluation_datasets d ON d.dataset_id = r.dataset_id
                WHERE r.run_id = ? AND r.organization_id = ?
                """,
                (run_id, organization_id),
            ).fetchone()
            result_rows = connection.execute(
                """
                SELECT * FROM rag_evaluation_results
                WHERE run_id = ? AND organization_id = ?
                ORDER BY created_at ASC
                """,
                (run_id, organization_id),
            ).fetchall()
        if row is None:
            raise ValueError("RAG evaluation run not found.")
        run = self._row_to_run(
            row,
            results=[self._row_to_result(result_row) for result_row in result_rows],
        )
        if role is not None:
            self.get_dataset(
                run.dataset_id,
                role=role,
                actor_id=actor_id,
                organization_id=organization_id,
            )
            if role != "admin" and run.created_by != actor_id:
                raise ValueError("RAG evaluation run not found.")
        return run

    def _execute_run(
        self,
        run_id: str,
        dataset: RagEvaluationDatasetRecord,
        corpus: list,
        provider: str,
        organization_id: str = "org_default",
    ) -> None:
        index_started = time.perf_counter()
        corpus_embeddings = embedding_service.embed_many(
            [row["text"] for row in corpus], provider=provider
        )
        index_latency_ms = (time.perf_counter() - index_started) * 1_000

        results: list[dict[str, object]] = []
        for case in dataset.cases:
            started = time.perf_counter()
            query_embedding = embedding_service.embed(case.question, provider=provider)
            matches: list[tuple[float, object]] = []
            for row, embedding in zip(corpus, corpus_embeddings):
                score = embedding_service.cosine_similarity(query_embedding, embedding)
                if score > dataset.minimum_score:
                    matches.append((score, row))
            matches.sort(key=lambda item: item[0], reverse=True)
            selected = matches[: dataset.top_k]
            latency_ms = (time.perf_counter() - started) * 1_000
            results.append(self._score_case(run_id, case, selected, latency_ms))

        retrieval_values = [float(result["retrieval_accuracy"]) for result in results]
        citation_values = [float(result["citation_correctness"]) for result in results]
        groundedness_values = [float(result["groundedness"]) for result in results]
        latency_values = [float(result["latency_ms"]) for result in results]
        hallucinations = sum(bool(result["hallucination_detected"]) for result in results)

        with get_connection() as connection:
            connection.executemany(
                """
                INSERT INTO rag_evaluation_results (
                    result_id, run_id, case_id, question, citations_json,
                    retrieval_accuracy, citation_correctness, groundedness,
                    hallucination_detected, latency_ms, error, organization_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        result["result_id"],
                        result["run_id"],
                        result["case_id"],
                        result["question"],
                        encode_json(result["citations"]),
                        result["retrieval_accuracy"],
                        result["citation_correctness"],
                        result["groundedness"],
                        result["hallucination_detected"],
                        result["latency_ms"],
                        None,
                        organization_id,
                    )
                    for result in results
                ],
            )
            connection.execute(
                """
                UPDATE rag_evaluation_runs
                SET status = ?, case_count = ?, retrieval_accuracy = ?,
                    citation_correctness = ?, groundedness = ?, hallucination_rate = ?,
                    average_latency_ms = ?, p95_latency_ms = ?, index_latency_ms = ?,
                    error = NULL, completed_at = ?
                WHERE run_id = ?
                """,
                (
                    "completed",
                    len(results),
                    _average(retrieval_values),
                    _average(citation_values),
                    _average(groundedness_values),
                    _percent(hallucinations / len(results)) if results else 0.0,
                    _average(latency_values),
                    _p95(latency_values),
                    round(index_latency_ms, 3),
                    datetime.now(timezone.utc).isoformat(),
                    run_id,
                ),
            )

    def _score_case(
        self,
        run_id: str,
        case: RagEvaluationCaseRecord,
        matches: list[tuple[float, object]],
        latency_ms: float,
    ) -> dict[str, object]:
        expected_chunks = set(case.expected_chunk_ids)
        expected_documents = set(case.expected_document_ids)

        def is_expected(row) -> bool:
            if expected_chunks:
                return row["chunk_id"] in expected_chunks
            return row["document_id"] in expected_documents

        citations = [
            {
                "document_id": row["document_id"],
                "chunk_id": row["chunk_id"],
                "title": row["title"],
                "excerpt": _excerpt(row["text"]),
                "score": round(score, 6),
                "expected": is_expected(row),
            }
            for score, row in matches
        ]
        if case.unanswerable:
            retrieval_accuracy = 100.0 if not citations else 0.0
            citation_correctness = 100.0 if not citations else 0.0
            groundedness = 100.0 if not citations else 0.0
            hallucination_detected = bool(citations)
        else:
            if expected_chunks:
                retrieved_expected = {
                    citation["chunk_id"]
                    for citation in citations
                    if citation["chunk_id"] in expected_chunks
                }
                retrieval_accuracy = _percent(
                    len(retrieved_expected) / len(expected_chunks)
                )
            else:
                retrieved_expected = {
                    citation["document_id"]
                    for citation in citations
                    if citation["document_id"] in expected_documents
                }
                retrieval_accuracy = _percent(
                    len(retrieved_expected) / len(expected_documents)
                )
            correct_citations = sum(bool(citation["expected"]) for citation in citations)
            citation_correctness = (
                _percent(correct_citations / len(citations)) if citations else 0.0
            )
            evidence = " ".join(str(citation["excerpt"]) for citation in citations)
            supported_facts = sum(
                _fact_supported(fact, evidence) for fact in case.expected_facts
            )
            groundedness = _percent(supported_facts / len(case.expected_facts))
            hallucination_detected = any(
                not bool(citation["expected"]) for citation in citations
            ) or bool(citations and groundedness == 0.0)

        return {
            "result_id": f"evalresult_{uuid4().hex}",
            "run_id": run_id,
            "case_id": case.case_id,
            "question": case.question,
            "citations": citations,
            "retrieval_accuracy": round(retrieval_accuracy, 2),
            "citation_correctness": round(citation_correctness, 2),
            "groundedness": round(groundedness, 2),
            "hallucination_detected": hallucination_detected,
            "latency_ms": round(latency_ms, 3),
        }

    def _create_run(
        self,
        comparison_id: str,
        dataset: RagEvaluationDatasetRecord,
        provider: str,
        created_by: str,
        organization_id: str = "org_default",
    ) -> str:
        run_id = f"evalrun_{uuid4().hex}"
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO rag_evaluation_runs (
                    run_id, comparison_id, dataset_id, provider, model,
                    status, case_count, created_by, organization_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    comparison_id,
                    dataset.dataset_id,
                    provider,
                    embedding_service.model_for_provider(provider),
                    "running",
                    dataset.case_count,
                    created_by,
                    organization_id,
                ),
            )
        return run_id

    def _finish_without_results(self, run_id: str, status: str, error: str) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE rag_evaluation_runs
                SET status = ?, error = ?, completed_at = ?
                WHERE run_id = ?
                """,
                (status, error[:2_000], datetime.now(timezone.utc).isoformat(), run_id),
            )

    def _load_corpus(
        self,
        document_ids: list[str],
        role: str,
        organization_id: str = "org_default",
    ) -> list:
        where = ["d.unsafe = ?", "d.organization_id = ?"]
        params: list[object] = [False, organization_id]
        if role != "admin":
            where.append("d.classification != ?")
            params.append("restricted")
        if document_ids:
            placeholders = ", ".join("?" for _ in document_ids)
            where.append(f"d.document_id IN ({placeholders})")
            params.extend(document_ids)

        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT c.chunk_id, c.text, c.chunk_index, d.document_id, d.title
                FROM document_chunks c
                JOIN documents d ON d.document_id = c.document_id
                WHERE {' AND '.join(where)}
                ORDER BY d.document_id ASC, c.chunk_index ASC
                """,
                tuple(params),
            ).fetchall()
        return list(rows)

    def _dataset_accessible(
        self,
        dataset: RagEvaluationDatasetRecord,
        role: str,
        actor_id: str | None,
        organization_id: str = "org_default",
    ) -> bool:
        if role == "admin":
            return True
        if actor_id is None or dataset.created_by != actor_id:
            return False
        if not dataset.document_ids:
            return True
        accessible_document_ids = {
            row["document_id"]
            for row in self._load_corpus(
                dataset.document_ids,
                role=role,
                organization_id=organization_id,
            )
        }
        return set(dataset.document_ids) <= accessible_document_ids

    def _row_to_case(self, row) -> RagEvaluationCaseRecord:
        return RagEvaluationCaseRecord(
            case_id=row["case_id"],
            dataset_id=row["dataset_id"],
            organization_id=row["organization_id"],
            position=row["position"],
            question=row["question"],
            expected_document_ids=decode_json(row["expected_document_ids_json"], []),
            expected_chunk_ids=decode_json(row["expected_chunk_ids_json"], []),
            expected_facts=decode_json(row["expected_facts_json"], []),
            reference_answer=row["reference_answer"],
            unanswerable=bool(row["unanswerable"]),
        )

    def _row_to_dataset(
        self, row, cases: list[RagEvaluationCaseRecord]
    ) -> RagEvaluationDatasetRecord:
        return RagEvaluationDatasetRecord(
            dataset_id=row["dataset_id"],
            organization_id=row["organization_id"],
            name=row["name"],
            description=row["description"],
            document_ids=decode_json(row["document_ids_json"], []),
            top_k=row["top_k"],
            minimum_score=float(row["minimum_score"]),
            created_by=row["created_by"],
            case_count=int(row["case_count"]),
            cases=cases,
            created_at=_as_string(row["created_at"]),
            updated_at=_as_string(row["updated_at"]),
        )

    def _row_to_result(self, row) -> RagEvaluationResultRecord:
        return RagEvaluationResultRecord(
            result_id=row["result_id"],
            run_id=row["run_id"],
            organization_id=row["organization_id"],
            case_id=row["case_id"],
            question=row["question"],
            citations=[
                RagEvaluationCitation(**citation)
                for citation in decode_json(row["citations_json"], [])
            ],
            retrieval_accuracy=float(row["retrieval_accuracy"]),
            citation_correctness=float(row["citation_correctness"]),
            groundedness=float(row["groundedness"]),
            hallucination_detected=bool(row["hallucination_detected"]),
            latency_ms=float(row["latency_ms"]),
            error=row["error"],
            created_at=_as_string(row["created_at"]),
        )

    def _row_to_run(
        self, row, results: list[RagEvaluationResultRecord]
    ) -> RagEvaluationRunRecord:
        return RagEvaluationRunRecord(
            run_id=row["run_id"],
            organization_id=row["organization_id"],
            comparison_id=row["comparison_id"],
            dataset_id=row["dataset_id"],
            dataset_name=row["dataset_name"],
            provider=row["provider"],
            model=row["model"],
            status=row["status"],
            case_count=int(row["case_count"]),
            retrieval_accuracy=float(row["retrieval_accuracy"]),
            citation_correctness=float(row["citation_correctness"]),
            groundedness=float(row["groundedness"]),
            hallucination_rate=float(row["hallucination_rate"]),
            average_latency_ms=float(row["average_latency_ms"]),
            p95_latency_ms=float(row["p95_latency_ms"]),
            index_latency_ms=float(row["index_latency_ms"]),
            error=row["error"],
            created_by=row["created_by"],
            results=results,
            created_at=_as_string(row["created_at"]),
            completed_at=_as_string(row["completed_at"]),
        )


rag_evaluation_service = RagEvaluationService()
