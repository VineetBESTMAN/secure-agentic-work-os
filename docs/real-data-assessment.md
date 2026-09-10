# Real-data quality and reliability assessment

Assessment window: 8–10 September 2026. Branch: `codex/real-data-assessment`.

## Release assessment

The identified ingestion, retrieval, answer-validation, resource-lifecycle, and dependency defects have regression coverage and fixes. Docker startup was recovered without resetting data, and real PostgreSQL retrieval and production configuration checks pass locally. This is **not a production certification**. Require the PR's CI gates and review before merging or deploying.

The branch intentionally includes the earlier, unpushed `956c03f` RAG-remediation commit. Remote `main` was checked before creating this branch; existing changes were preserved. No normal application database, document store, Docker volume, or external provider account was modified by the assessment. Push, PR creation, and merge remain manual.

## What was tested

The public-document benchmark uploads five unmodified source files through the authenticated HTTP API, then queries the actual ingestion/retrieval/generation path:

- [RFC 2606: Reserved Top Level DNS Names](https://www.rfc-editor.org/rfc/rfc2606.txt).
- [RFC 8259: JSON](https://www.rfc-editor.org/rfc/rfc8259.txt).
- [RFC 9309: Robots Exclusion Protocol](https://www.rfc-editor.org/rfc/rfc9309.txt).
- The repository README and frontend package manifest.

The RFCs are downloaded without changes and verified against committed SHA-256 hashes. Copyright notices remain in the cached originals. Private customer documents were not supplied or used. Malformed uploads, malicious instructions, table encodings, and resource failures use explicitly synthetic test fixtures.

The corpus, 22 questions, expected facts, and source expectations are in [`public_corpus.json`](../scripts/rag/public_corpus.json). Eighteen positive cases require an answer, the expected source citation, and every specified fact. Four negative cases require refusal and no citations. Questions and expected facts were not weakened between runs.

The expanded assessment started at **17/22** and reached **22/22** after remediation. It is now a regression set used during development, not an independent held-out estimate of accuracy. The older five-case repository benchmark also retains full retrieval/citation scores, with 96.668% heuristic answer correctness, no detected hallucinations, the hostile document quarantined, and no restricted-document citation exposed to the employee test account.

Machine-readable release results, including exact source hashes, per-case outcomes, environment, ingestion times, and query latency, are in [`verification/real-data-2026-09-09.json`](verification/real-data-2026-09-09.json). Full answers/excerpts are deliberately kept out of the committed summary; a local run retains them for review under `backend/data/`.

The semantic runs use local `BAAI/bge-small-en-v1.5` embeddings, two inference threads, and deterministic extractive generation. No OpenAI charges or live connector actions were incurred. The committed 22-case release run measured about 691 ms median and 938 ms p95 query latency on this Windows development machine. The lexical control also passed 22/22. These small-sample measurements are not an SLA or a scale benchmark. CPU ingestion remained noticeably slower than queries (the README took about 71 seconds); concurrent local verification also affected timing.

## Defects and remediation

| Area | Reproduced problem | Remediation and evidence |
| --- | --- | --- |
| Database resources | SQLite transaction contexts did not close connections; PostgreSQL cleanup could be skipped if commit failed | Explicit connection ownership and `finally` cleanup; commit/rollback closure regressions |
| Upload safety | Parsing happened before binary security preflight | Preflight before extraction for upload, replacement, and reindex; executable-disguised-as-PDF regression |
| Text inspection | Prompt scanning stopped after 20,000 characters and omitted headings; DLP silently ignored its unscanned tail | Scan all extracted content including headings; fail closed with an actionable message if the UTF-8 text exceeds the configured DLP limit |
| Parsing | UTF-16 CSV became corrupted, rows lost column labels, malformed PDFs produced internal errors | BOM-aware decoding, labeled CSV rows with locators, normalized parse errors returned as HTTP 400 |
| Chunking | Adjacent sections were merged despite configured bounds; Markdown comments inside code fences became headings | Preserve section boundaries, honor explicit chunk sizes, recognize code fences, retain paragraph/row provenance |
| Structured data | Small JSON fields were diluted by unrelated configuration content | Top-level JSON sections with key locators; named-file field matching in extractive selection |
| Indexed retrieval | Custom stems did not match SQLite FTS tokens; PostgreSQL required every query word | Porter/unicode61 FTS migration, database-native query tokenization, bounded PostgreSQL OR candidate retrieval |
| Search consistency | PostgreSQL dense/lexical candidate merging lost valid scores and mixed unlike lexical scales | Preserve real dense scores; rerank the bounded union consistently; exclude mismatched chunk tenants in search and evaluation |
| Citation validation | Whitespace quotes, changed quantities, and inverted claims could pass weak overlap checks | Reject empty support, require substantive support, check numeric literals and polarity; regression tests use relevant questions |
| Answer selection | Related metadata or a generic sentence was labeled as an answer; late evidence was ignored | Body evidence required, discriminative term weighting, bounded prose passages, retained qualifiers, conservative answer-type checks, configured relevance threshold respected |
| HTTP concurrency | Synchronous ingestion and queue work ran on the async request loop | Run blocking work in the thread pool; test verifies ingestion executes off the event-loop thread |
| Embeddings | Network clients were not closed; malformed batches could be accepted; initial model loading could race | Close clients on success/failure, validate indices/count/dimensions/finite values, initialize under the model lock |
| Evaluation fidelity | Re-embedded evaluation chunks omitted the heading prefix used at ingestion | Identical section prefix for evaluation and stored embedding inputs |
| Test isolation | Benchmarks could load developer `.env`/secret-file settings; pytest traversed runtime caches | Clear application configuration aliases before isolated runs; disable `.env`; constrain collection to `tests/` |
| Dependencies | Browserslist and pypdf had known vulnerabilities | Lockfile updates to Browserslist 4.28.9; require pypdf >=6.16.1, verified locally with 6.18.0; fresh audits report no known dependency vulnerabilities |

OpenAI response-contract checks were verified against the [official embeddings API reference](https://developers.openai.com/api/reference/python/resources/embeddings/methods/create) and the installed SDK cleanup implementation. The OpenAI Docs skill guided that check. Provider-response tests are mocked, not evidence of live account access.

## Verification and remaining gates

- Backend on Windows: 132 passed, one optional PostgreSQL test skipped in the full suite; the PostgreSQL test separately passed against a disposable pgvector container. The test skips without a dedicated URL.
- Backend in the Python 3.10 Linux image: 130 tests passed, including PostgreSQL. Three repository-configuration tests initially failed because their files were absent from the application image; all three passed when rerun with those fixtures mounted read-only (133 tests verified in total). No application change or weakened assertion was needed for those failures.
- Frontend: production build passed after the lockfile patch; npm audit reports zero vulnerabilities.
- Backend dependencies: pip-audit reports no known vulnerabilities after the PDF parser patch. The local application package is not on PyPI and therefore is not itself covered by that advisory database.
- Database: SQLite upgrade/downgrade and preservation tests pass, including FTS rebuild and update-trigger behavior. No destructive migration was run against the normal database.
- Compose and operations: development and production/restore Compose checks pass. Container-backed validation of Prometheus configuration and nine alert rules, Alertmanager, and Caddy also passes.
- Docker runtime: backend and production frontend image builds pass. Nginx configuration and the frontend's absence of a hard-coded local API URL pass. An isolated backend container serves health, login, upload, and a correctly cited query over loopback HTTP.
- PostgreSQL: the dedicated integration test passes locally against `pgvector/pgvector:pg16`, covering actual query execution, legacy-model lexical fallback, restricted/tenant boundaries, and migration preservation. A dedicated CI service now runs this test too. Its database is disposable and stored in tmpfs, not a normal volume.
- External systems: live Google, Slack, GitHub, Jira, Notion, OpenClaw, production object storage, malware daemon, backups, restore exercises, and production deployment were not exercised in this assessment.

## Reproduce safely

From Git Bash at the repository root, with the backend virtual environment installed:

```bash
./backend/.venv/Scripts/python.exe scripts/rag/assess_real_data.py \
  --download \
  --output backend/data/real-data-release.json \
  --summary-output docs/verification/real-data-2026-09-09.json \
  --strict

cd backend
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m pip_audit
cd ../frontend
npm ci
npm run build
npm audit --audit-level=moderate
cd ..
```

Use `--backend lexical` for the offline lexical control and `--threads N` for an explicitly measured CPU configuration. The lexical control is not a substitute for semantic-model verification. Cached RFCs must match the pinned hashes; changed or missing sources fail clearly. FastEmbed may need to download the local model on its first run.

For the PostgreSQL integration test, provision a **new disposable database** whose name begins with `workos_test_`, set `WORKOS_TEST_POSTGRES_URL`, and run `tests/test_postgres_retrieval.py`. Do not point it at a normal database: the test writes fixtures and exercises a migration downgrade/upgrade. CI provisions the disposable service automatically.

## Local Docker recovery

Docker Desktop could not initialize its inference and secrets services because Windows could not access existing runtime socket entries (`dockerInference` and `engine.sock`). This is consistent with the symptom in [Docker Desktop issue 625](https://github.com/docker/desktop-feedback/issues/625), not evidence of an application database fault. See also the [official troubleshooting guide](https://docs.docker.com/desktop/troubleshoot-and-support/troubleshoot/).

After stopping Docker's processes with approval and checking the affected directories contained runtime sockets only, the directories were renamed and fresh runtime directories created. Preserved originals are under `%LOCALAPPDATA%/Docker/run.recovery-20260910`, `%LOCALAPPDATA%/Docker/run.recovery-20260910-retry`, and `%LOCALAPPDATA%/docker-secrets-engine.recovery-20260910`. Docker then started successfully (engine 29.6.1). No factory reset, volume prune, WSL unregister, or application-data deletion was performed. This repairs the observed local startup state; it does not establish that the underlying Docker issue cannot recur. Diagnostic data was not uploaded.

## Rollout and rollback

1. Review both commits relative to `main`, then require all PR checks, including `postgres-retrieval` and production-image validation, to pass.
2. Back up the application database and original document storage using the normal operational procedure before deployment. Verify the backup separately; this assessment did not validate production backups.
3. Apply migration `20260908_0015` through the normal deployment migration step. It rebuilds only the derived SQLite FTS index; PostgreSQL is a no-op. Do not delete or recreate the database or Docker volumes.
4. Existing source files and chunks are retained. Reindex selected documents through the tenant-authorized API/UI to adopt new CSV, Markdown, and JSON chunking. Reindex changes chunk IDs, so recreate evaluation datasets that pin old chunk IDs after checking their source expectations.
5. Reindexing may now reject documents that exceed the DLP inspection limit. Split them or deliberately adjust `APP_DLP_SCAN_MAX_BYTES`; do not silently disable inspection.
6. Validate a small representative document set and the approval/security workflow before broad rollout. Roll back the application image if needed; use a verified backup for data restoration only when authorized. The tested tokenizer downgrade preserves source rows, but it is not a general production rollback recommendation.

## Limits and optimization priorities

The deterministic answer path is extractive and conservative. Exact quotes, numeric checks, and lexical support do **not** prove semantic entailment. Multi-hop answers, unusual wording, indirect negation, conflicting documents, multilingual content, and unknown-answer detection need a larger human-reviewed held-out dataset. Confidence is a ranking heuristic, not a calibrated probability. A successful 22-case regression run does not make arbitrary answers safe to act on; policy and approval gates remain mandatory.

CPU ingestion is the main measured performance concern. Prefer the existing queued-upload path for substantial collections, warm the model once per worker, bound worker concurrency, and measure throughput/RAM on the deployment hardware before changing thread counts. Preserve section quality rather than merging unrelated content solely to reduce inference work. SQLite's bounded dense scan is not equivalent to full-collection ANN retrieval; large deployments require the PostgreSQL path and representative load testing.

Scanned/image-only PDFs require OCR outside the current extraction path. Complex DOCX tables, multilingual/domain-specific documents, real customer permission structures, and live provider behavior remain acceptance work, not claims established by this public English-text corpus. Secrets, tenant data, and external side effects should only enter that testing after explicit owner approval and with disposable accounts where possible.

The fresh Linux dependency resolution emitted upstream Starlette/AnyIO deprecation warnings and an MCP warning about future resource-audience validation defaults. Current MCP authentication verifies Work OS sessions or scoped OpenClaw credentials, but does not implement a separate resource-bound token audience. Plan and test that token-contract change before adopting MCP 3.x; do not silence the warning by claiming audience validation already exists. Advisory scans and passing tests are not a substitute for a formal authentication/security review.
