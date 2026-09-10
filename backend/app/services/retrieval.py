import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence

from app.core.config import get_settings
from app.services.embeddings import embedding_service


TOKEN_PATTERN = re.compile(
    r"[^\W_](?:[\w+.-]*[^\W_])?", re.UNICODE
)
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "does",
    "document",
    "documents",
    "configured",
    "exact",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "performed",
    "policy",
    "that",
    "the",
    "this",
    "to",
    "version",
    "versions",
    "we",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "should",
    "normally",
}


def _stem(token: str) -> str:
    concepts = {
        "approval": "approv",
        "approvals": "approv",
        "approve": "approv",
        "approved": "approv",
        "approves": "approv",
        "mandatory": "requir",
        "must": "requir",
        "need": "requir",
        "needed": "requir",
        "needs": "requir",
        "require": "requir",
        "required": "requir",
        "requires": "requir",
    }
    if token in concepts:
        return concepts[token]
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        root = token[:-3]
        if len(root) > 2 and root[-1] == root[-2]:
            root = root[:-1]
        return root
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def retrieval_terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in TOKEN_PATTERN.findall(text.casefold()):
        # Match human questions to filenames/identifiers without losing exact tokens.
        parts = re.split(r"[-./]", token)
        for part in dict.fromkeys([token, *parts]):
            if part and part not in STOP_WORDS:
                terms.append(_stem(part))
    return terms


def full_text_terms(text: str) -> list[str]:
    """Let the database tokenizer stem its input; never send custom stems to FTS."""
    return list(dict.fromkeys(
        term for term in re.findall(r"[^\W_]+", text.casefold(), re.UNICODE)
        if term not in STOP_WORDS
    ))[:20]


def term_coverage(question: str, evidence: str) -> float:
    question_terms = set(retrieval_terms(question))
    if not question_terms:
        return 0.0
    evidence_terms = set(retrieval_terms(evidence))
    return len(question_terms & evidence_terms) / len(question_terms)


def _row_value(row, key: str, default=None):
    try:
        value = row[key]
    except (IndexError, KeyError, TypeError):
        return default
    return default if value is None else value


def searchable_text(row) -> str:
    return "\n".join(
        part
        for part in (
            str(_row_value(row, "title", "")),
            str(_row_value(row, "heading", "")),
            str(_row_value(row, "text", "")),
        )
        if part
    )


@dataclass(frozen=True)
class RankedMatch:
    row: object
    score: float
    dense_score: float
    lexical_score: float
    term_coverage: float


class HybridRetrievalService:
    def rank(
        self,
        *,
        question: str,
        rows: Sequence,
        query_embedding: list[float] | None = None,
        embeddings: Sequence[list[float] | None] | None = None,
        dense_scores: Mapping[str, float] | None = None,
        lexical_scores: Mapping[str, float] | None = None,
        top_k: int | None = None,
        minimum_score: float | None = None,
    ) -> list[RankedMatch]:
        if not rows:
            return []
        settings = get_settings()
        top_k = top_k or settings.rag_top_k
        minimum_score = max(
            settings.rag_minimum_score,
            minimum_score if minimum_score is not None else 0.0,
        )

        query_terms = retrieval_terms(question)
        documents = [retrieval_terms(searchable_text(row)) for row in rows]
        bm25_values = self._bm25(query_terms, documents)
        maximum_bm25 = max(bm25_values, default=0.0)
        external_maximum = max((lexical_scores or {}).values(), default=0.0)

        ranked: list[RankedMatch] = []
        for index, row in enumerate(rows):
            chunk_id = str(_row_value(row, "chunk_id", index))
            evidence = searchable_text(row)
            coverage = term_coverage(question, evidence)
            phrase_score = self._ordered_pair_coverage(query_terms, documents[index])

            raw_lexical = (
                float(lexical_scores.get(chunk_id, 0.0))
                if lexical_scores is not None
                else bm25_values[index]
            )
            normalizer = external_maximum if lexical_scores is not None else maximum_bm25
            normalized_bm25 = raw_lexical / normalizer if normalizer > 0 else 0.0
            lexical = min(
                1.0,
                0.58 * normalized_bm25 + 0.30 * coverage + 0.12 * phrase_score,
            )

            dense: float | None = None
            if dense_scores is not None and chunk_id in dense_scores:
                dense = max(0.0, min(1.0, float(dense_scores[chunk_id])))
            elif (
                query_embedding is not None
                and embeddings is not None
                and index < len(embeddings)
                and embeddings[index]
            ):
                dense = max(
                    0.0,
                    min(
                        1.0,
                        embedding_service.cosine_similarity(
                            query_embedding, embeddings[index] or []
                        ),
                    ),
                )

            if dense is None:
                combined = 0.80 * lexical + 0.20 * coverage
                relevant_signal = coverage >= settings.rag_minimum_term_coverage
                dense_value = 0.0
            else:
                combined = 0.42 * dense + 0.48 * lexical + 0.10 * coverage
                relevant_signal = (
                    coverage >= settings.rag_minimum_term_coverage
                    or dense >= settings.rag_minimum_dense_score
                )
                dense_value = dense

            if combined >= minimum_score and relevant_signal:
                ranked.append(
                    RankedMatch(
                        row=row,
                        score=round(combined, 6),
                        dense_score=round(dense_value, 6),
                        lexical_score=round(lexical, 6),
                        term_coverage=round(coverage, 6),
                    )
                )

        ranked.sort(
            key=lambda match: (
                match.score,
                match.term_coverage,
                match.lexical_score,
                match.dense_score,
                str(_row_value(match.row, "created_at", "")),
            ),
            reverse=True,
        )
        return ranked[:top_k]

    @staticmethod
    def _bm25(query_terms: list[str], documents: list[list[str]]) -> list[float]:
        if not query_terms or not documents:
            return [0.0 for _ in documents]
        document_count = len(documents)
        average_length = sum(len(document) for document in documents) / document_count
        average_length = max(1.0, average_length)
        document_frequency = Counter(
            term for term in set(query_terms) for document in documents if term in document
        )
        k1 = 1.5
        b = 0.75
        scores: list[float] = []
        for document in documents:
            frequencies = Counter(document)
            score = 0.0
            for term in set(query_terms):
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                frequency_in_documents = document_frequency[term]
                inverse_frequency = math.log(
                    1
                    + (document_count - frequency_in_documents + 0.5)
                    / (frequency_in_documents + 0.5)
                )
                denominator = frequency + k1 * (
                    1 - b + b * len(document) / average_length
                )
                score += inverse_frequency * frequency * (k1 + 1) / denominator
            scores.append(score)
        return scores

    @staticmethod
    def _ordered_pair_coverage(query: list[str], document: list[str]) -> float:
        pairs = list(zip(query, query[1:]))
        if not pairs:
            return 0.0
        document_pairs = set(zip(document, document[1:]))
        return sum(pair in document_pairs for pair in pairs) / len(pairs)


hybrid_retrieval_service = HybridRetrievalService()
