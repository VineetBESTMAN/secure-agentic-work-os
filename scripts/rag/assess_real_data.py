"""Real, unchanged public documents through isolated HTTP upload/query APIs.

This is an acceptance benchmark, not a claim of semantic correctness. Expected
facts are literal checks; all answers and citations are retained for human review.
Run in a fresh process. No application .env, database, or credentials are loaded.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone


def normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def configure(root: Path, temporary: Path, backend: str, threads: int) -> None:
    if "app.main" in sys.modules:
        raise RuntimeError("Run this assessment in a fresh Python process.")
    sys.path.insert(0, str(root / "backend"))
    from app.core.config import Settings
    for field in Settings.model_fields.values():
        if isinstance(field.validation_alias, str):
            os.environ.pop(field.validation_alias, None)
    Settings.model_config["env_file"] = None
    for name in list(os.environ):
        if name.startswith(("APP_", "OPENAI_", "DATABASE_", "REDIS_")):
            del os.environ[name]
    os.environ.update({
        "APP_DATABASE_PATH": str(temporary / "assessment.db"),
        "APP_LANGGRAPH_SQLITE_PATH": str(temporary / "checkpoints.db"),
        "APP_UPLOAD_DIR": str(temporary / "uploads"),
        "APP_SECRET_KEY": "isolated-public-assessment-not-a-production-key",
        "APP_ACCESS_TOKEN_EXPIRE_MINUTES": "120",
        "APP_OBJECT_STORAGE_BACKEND": "local",
        "APP_ASYNC_JOBS_ENABLED": "false",
        "APP_RATE_LIMIT_ENABLED": "false",
        "APP_EMBEDDING_PROVIDER": "local",
        "APP_LOCAL_EMBEDDING_BACKEND": backend,
        "APP_LOCAL_EMBEDDING_CACHE_DIR": str(root / "backend/data/fastembed-cache"),
        "APP_LOCAL_EMBEDDING_THREADS": str(threads),
        "APP_MODEL_PROVIDER": "deterministic",
    })


def corpus(root: Path, manifest: dict, download: bool) -> list[tuple[str, bytes, str]]:
    directory = root / "backend/data/assessment-corpus"
    directory.mkdir(parents=True, exist_ok=True)
    documents = []
    for source in manifest["sources"]:
        path = directory / source["name"]
        if not path.exists():
            if not download:
                raise RuntimeError(f"Missing {path}; run with --download to fetch public RFCs.")
            import httpx
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                response = client.get(source["url"])
                response.raise_for_status()
            data = response.content
        else:
            data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != source["sha256"]:
            raise RuntimeError(f"Checksum mismatch for {source['name']}; refusing changed corpus.")
        if not path.exists():
            path.write_bytes(data)
        documents.append((source["name"], data, source["url"]))
    documents.extend([
        ("workos-readme.md", (root / "README.md").read_bytes(), "README.md"),
        ("frontend-package.json", (root / "frontend/package.json").read_bytes(), "frontend/package.json"),
    ])
    return documents


def run(root: Path, manifest: dict, documents: list, backend: str, threads: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="workos-real-data-") as temporary:
        configure(root, Path(temporary), backend, threads)
        from fastapi.testclient import TestClient
        from app.main import app
        from app.core.config import get_settings

        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"email": "admin@demo.local", "password": "demo-password"})
            login.raise_for_status()
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            ingestion = []
            identifiers = {}
            for name, data, source in documents:
                started = time.perf_counter()
                response = client.post("/api/documents/upload", headers=headers,
                    files={"file": (name, data, "text/plain")},
                    data={"classification": "public", "owner_team": "assessment"})
                response.raise_for_status()
                record = response.json()
                identifiers[record["document_id"]] = name
                ingestion.append({"name": name, "source": source, "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(), "chunks": record["chunk_count"],
                    "quarantined": record["unsafe"],
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2)})
                print(f"Ingested {name}: {record['chunk_count']} chunks", file=sys.stderr, flush=True)

            results = []
            for case in manifest["cases"]:
                started = time.perf_counter()
                response = client.post("/api/documents/query", headers=headers,
                    json={"question": case["question"]})
                elapsed = round((time.perf_counter() - started) * 1000, 2)
                response.raise_for_status()
                answer = response.json()
                cited_sources = [identifiers.get(c["document_id"], "seed-document") for c in answer["citations"]]
                missing = [fact for fact in case.get("facts", []) if normalize(fact) not in normalize(answer["answer"])]
                source_hit = case.get("source") in cited_sources if not case.get("unanswerable") else None
                passed = (not answer["answerable"] and not answer["citations"]) if case.get("unanswerable") else (
                    answer["answerable"] and answer["grounded"] and source_hit and not missing)
                results.append({**case, "passed": bool(passed), "answer": answer["answer"],
                    "answerable": answer["answerable"], "source_hit": source_hit,
                    "missing_facts": missing, "latency_ms": elapsed,
                    "citations": [{"source": identifiers.get(c["document_id"], "seed-document"),
                        "locator": c["locator"], "score": c["score"], "excerpt": c["excerpt"]}
                        for c in answer["citations"]]})
                if not passed and not case.get("unanswerable"):
                    from app.services.embeddings import embedding_service
                    from app.services.rag import rag_service
                    settings = get_settings()
                    original_top_k = settings.rag_top_k
                    try:
                        settings.rag_top_k = 12
                        matches = rag_service._hybrid_matches(
                            question=case["question"],
                            query_embedding=embedding_service.embed(case["question"], input_type="query"),
                            role="admin", organization_id="org_default")
                        results[-1]["diagnostic_candidates"] = [{
                            "source": identifiers.get(m.row["document_id"], "seed-document"),
                            "score": m.score, "text": m.row["text"], "heading": m.row["heading"],
                        } for m in matches]
                    finally:
                        settings.rag_top_k = original_top_k
                print(f"{'PASS' if passed else 'FAIL'} {case['id']}: {elapsed} ms", file=sys.stderr, flush=True)

            positive = [r for r in results if not r.get("unanswerable")]
            negative = [r for r in results if r.get("unanswerable")]
            latency = sorted(r["latency_ms"] for r in results)
            return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
                "environment": {"python": platform.python_version(), "platform": platform.platform(),
                    "embedding_backend": backend, "model": get_settings().local_embedding_model,
                    "embedding_threads": threads, "generation": "deterministic",
                    "fastembed": importlib.metadata.version("fastembed")},
                "isolation": "Fresh disposable SQLite, uploads, checkpoints; no .env or provider secrets",
                "ingestion": ingestion, "summary": {"total": len(results),
                    "passed": sum(r["passed"] for r in results),
                    "answer_fact_passes": sum(r["passed"] for r in positive),
                    "answer_cases": len(positive), "correct_refusals": sum(r["passed"] for r in negative),
                    "refusal_cases": len(negative), "p50_latency_ms": statistics.median(latency),
                    "p95_latency_ms": latency[min(len(latency)-1, int(len(latency)*.95))]},
                "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Fetch only missing checksum-pinned public RFCs")
    parser.add_argument("--backend", choices=["fastembed", "lexical"], default="fastembed")
    parser.add_argument("--threads", type=int, default=2, choices=range(1, 65))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, help="Optional shareable report without answer/excerpt text")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero if any acceptance case fails")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads(Path(__file__).with_name("public_corpus.json").read_text(encoding="utf-8"))
    result = run(root, manifest, corpus(root, manifest, args.download), args.backend, args.threads)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.summary_output:
        summary = {key: value for key, value in result.items() if key != "results"}
        summary["cases"] = [{key: case[key] for key in ("id", "passed", "answerable", "source_hit", "latency_ms")}
                            for case in result["results"]]
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))
    if args.strict and result["summary"]["passed"] != result["summary"]["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
