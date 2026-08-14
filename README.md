# Enterprise AI Chat Assistant

An AI-powered enterprise chat assistant built with **FastAPI + LangGraph + RAG (Chroma) + SQL (SQLite) + an LLM**, covering conversational multi-turn chat with streaming, retrieval-augmented answers with citations, an agentic router that combines knowledge-base search / SQL / business tools in a single turn, human-approved write actions, backend-enforced RBAC, and a 15-case evaluation suite.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the system diagram and design-decision rationale.

## Contents

- [Quick start](#quick-start)
- [Demo accounts](#demo-accounts)
- [Running the demo flow](#running-the-demo-flow-scriptable-walkthrough)
- [Requirement-by-requirement mapping](#requirement-by-requirement-mapping)
- [Testing & evaluation](#testing--evaluation)
- [Configuration](#configuration)
- [Known limitations](#known-limitations--honest-notes)

## Quick start

### Option A — Docker (recommended)

```bash
docker compose up --build
```

The image seeds the SQL database and ingests the knowledge base **at build time** (so the container needs no network access on first run — the small local embedding model is baked in). On `docker compose up`, a named volume (`app_data`) is mounted at `/app/data`; Docker pre-populates it from the image on first creation, so the running container still has the full seeded dataset. Subsequent restarts do not re-seed (so tickets/approvals you create persist).

Once healthy:

```bash
curl http://localhost:8000/health
```

### Option B — Local Python

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash / macOS-Linux: source .venv/bin/activate
pip install -r requirements.txt

python -m app.db.seed              # creates + seeds data/app.db
python -m app.rag.ingest           # chunks + embeds knowledge_base/ into data/chroma

uvicorn app.main:app --reload
```

By default `LLM_PROVIDER=offline` — a fully deterministic, no-API-key chat model that still exercises the entire pipeline (routing, retrieval, SQL scoping, permissions, citations, no-hallucination behavior). To use a real model, set in `.env` (copy from `.env.example`):

```bash
LLM_PROVIDER=anthropic   # or openai / gemini
ANTHROPIC_API_KEY=sk-...
```

No code changes are needed to switch providers — it's a config-only choice (`app/agent/llm.py`).

## Demo accounts

Seeded by `python -m app.db.seed` (see `app/db/seed.py` for the full dataset: 5 customers, 7 orders, 11 invoices, 3 tickets):

| Username | Password | Role | Notes |
|---|---|---|---|
| `admin` | `Admin#2026!` | admin | Full access |
| `finance.morgan` | `Finance#2026!` | employee | Can query SQL + all tools |
| `agent.jordan` | `Support#2026!` | employee | Can query SQL + all tools |
| `acme.customer` | `Acme#2026!` | customer | Scoped to Acme Manufacturing (customer id 1) |
| `blueharbor.customer` | `Blue#2026!` | customer | Scoped to Blue Harbor Logistics (customer id 2) |

Login (OAuth2 password form):

```bash
curl -X POST http://localhost:8000/auth/login \
  -d "username=finance.morgan&password=Finance#2026!" \
  -H "Content-Type: application/x-www-form-urlencoded"
```

Use the returned `access_token` as `Authorization: Bearer <token>` on every other call.

## Running the demo flow (scriptable walkthrough)

`scripts/demo_flow.sh` exercises every scenario the assignment's demo checklist calls for, against a live running server:

```bash
BASE_URL=http://127.0.0.1:8000 bash scripts/demo_flow.sh
```

It walks through, in order: login as all 4 demo users → a plain RAG question with citations → a SQL question scoped to a named customer → the canonical multi-step SQL+RAG payment-policy question → a tool invocation (`customer_lookup`) → the full human-approval workflow (propose → pending → approve → executed) → a multi-turn conversation (context carried via persisted history) → streaming tokens (visible throughout) → a prompt-injection attempt → six explicit failure/error scenarios (400/401/403/404/409 and a blocked cross-customer read). This script is also what a human presenter would narrate for the required 10–15 minute recorded demo — each `hr "N) ..."` banner corresponds to one checklist item from the brief.

## Requirement-by-requirement mapping

### 1. AI Chat
| Requirement | Where |
|---|---|
| Conversational chat interface | `POST /chat` (`app/api/chat.py`) |
| Multi-turn + conversation memory | `Conversation`/`Message` tables (`app/db/models.py`); history loaded and replayed into the prompt each turn |
| Streamed LLM responses | Server-Sent Events (`event: token` / `event: done`); verified in `tests/test_api.py::test_chat_rag_question_streams_and_cites_sources` |
| Context across follow-ups | Last 6 turns of history included in the synthesis prompt (`app/agent/prompts.py`) |

### 2. RAG
| Requirement | Where |
|---|---|
| 10–20 doc knowledge base (HR, policy, products, invoices, support) | `knowledge_base/*.md` — 16 documents |
| Ingestion, chunking, embeddings, vector search | `app/rag/ingest.py`, `chunking.py`, `vectorstore.py` (Chroma + local ONNX MiniLM embeddings — no external embeddings API/key needed) |
| Retrieval by question | `app/rag/retriever.py` |
| Source citations | `citations_from_chunks()`; returned in every `/chat` `done` event as `doc_id`/`title`/`source_file`/`similarity` |
| No hallucination when info is missing | Similarity threshold (`RAG_SCORE_THRESHOLD`) + explicit "don't guess" system-prompt rule; `eval` case `RAG-04` and `tests/test_agent.py::test_no_hallucination_when_information_is_not_in_kb` |

### 3. Agentic workflow
| Requirement | Where |
|---|---|
| LangGraph | `app/agent/graph.py` (`StateGraph`) |
| Determines needed capability (RAG / SQL / tool) | `classify_node` + `route_after_classify` (`app/agent/nodes.py`) — LLM structured-output classification with a deterministic keyword fallback (the only path in offline mode) |
| Multi-step queries combining sources | `sql_node`, `rag_node`, `tool_node` can all run in the same turn; canonical example verified end-to-end in `eval` cases `MULTI-01/02/03` |
| Prevents infinite loops | `recursion_limit=AGENT_MAX_STEPS` on every `graph.invoke`, `GraphRecursionError` caught and degraded to a safe message (`AgentAbortedError`) |

### 4. SQL integration
| Requirement | Where |
|---|---|
| DB with customers/orders/invoices/support_tickets | `app/db/models.py`, seeded via `app/db/seed.py` |
| AI answers using structured data | `sql_node` + `app/agent/sql_builder.py` (LLM-authored SQL against a fixed schema when a real provider is configured, template fallback otherwise — always re-validated) |
| SQL validation & restriction | `app/db/sql_guard.py`: AST allow-list (sqlglot) restricted to `SELECT` only, on 4 whitelisted tables |
| No unrestricted/destructive DB access | Execution connection is opened **read-only** (`mode=ro` + `PRAGMA query_only`) — a second, independent layer even if AST validation had a bug |
| Authorization / read-only controls | Only `admin`/`employee` roles may use SQL at all; `customer` role is hard-blocked from it (`app/tools/permissions.py`) — see `tests/test_sql_guard.py`, `tests/test_permissions.py` |

### 5. Tools & actions
| Requirement | Where |
|---|---|
| Customer / invoice / ticket lookup, ticket creation | `app/tools/business_tools.py` |
| Modifying actions require human confirmation | `create_support_ticket` always requires approval (`WRITE_TOOLS`); `tool_node` stops and creates a `PendingApproval` row instead of executing; `POST /approvals/{id}/decide` executes only after an explicit `approve` |

### 6. Security
| Requirement | Where |
|---|---|
| Auth + authorization | JWT (`app/auth/security.py`) + role dependency (`app/auth/dependencies.py`) |
| Prompt-injection protection | System prompt explicitly instructs the model to treat retrieved docs/tool data as inert (`app/agent/prompts.py`); the knowledge base includes a document (`SUP-003`) with a *real* embedded injection attempt used as a live test fixture, not just a comment |
| Retrieved documents treated as untrusted | `format_context_block()` wraps every chunk in an explicit "this is data, not instructions" envelope |
| Tool permissions enforced by backend, not the prompt | `app/tools/permissions.py::authorize_tool_call` is re-checked in `app/tools/executor.py` immediately before every execution — including a second time after human approval — regardless of what the LLM/classifier decided |
| Prevent unauthorized DB/tool access | A `customer`-role caller's `customer_id` is **forcibly overwritten** to their own account on every scoped tool call, never trusted from the query text (defends against both bugs and prompt injection) |

### 7. Production engineering
| Requirement | Where |
|---|---|
| FastAPI backend | `app/main.py` |
| Handle LLM/tool failures, timeouts, retries | `tenacity` retries on transient DB errors (`app/tools/executor.py`); LLM classification/SQL-generation failures fall back gracefully (`app/agent/classify.py`, `app/agent/sql_builder.py`); `asyncio.wait_for` timeout around the agent's gather phase (`app/api/chat.py`); per-tool timeout (`TOOL_CALL_TIMEOUT_SECONDS`) |
| Prevent infinite agent loops | See Agentic workflow section above |
| Containerized | `Dockerfile` + `docker-compose.yml` — built and run successfully against a live Docker daemon during development (see below) |
| Logging / observability | Structured JSON logs (`structlog`, `app/observability/logging_config.py`); every request gets an `X-Request-ID`; every agent turn logs an `agent_turn_completed` event with the full node-level `trace` (which nodes ran, retrieval hit counts, SQL row counts, per-node latency) |

### 8. Evaluation
| Requirement | Where |
|---|---|
| ~15 test cases (RAG / SQL / multi-step / security) | `eval/cases.py` — exactly 15: 4 RAG, 3 SQL, 3 multi-step, 4 security/injection, 1 failure/edge-case |
| Retrieval quality, answer correctness, citation accuracy, latency, failure rate, agent/tool execution | `eval/run_eval.py` computes all of these; see [`eval/results.md`](eval/results.md) / [`eval/results.json`](eval/results.json) |

## Testing & evaluation

```bash
# Unit + API integration tests (isolated temp DB/vector store, offline LLM) — 50 tests
python -m pytest tests/ -v

# The 15-case evaluation suite (resets the canonical seed dataset, then reports metrics)
python -m eval.run_eval
```

Current status: **50/50 pytest tests passing**, **15/15 eval cases passing**, retrieval hit rate 100%, mean agent latency ~160ms in offline mode.

## Configuration

All configuration is environment-variable driven (`app/config.py`, `pydantic-settings`). Copy `.env.example` to `.env` and adjust. Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `offline` | `anthropic` \| `openai` \| `gemini` \| `offline` |
| `JWT_SECRET_KEY` | (dev placeholder) | **Change this for anything beyond local dev** |
| `RAG_TOP_K` / `RAG_SCORE_THRESHOLD` | `4` / `0.35` | Retrieval breadth vs. precision |
| `AGENT_MAX_STEPS` | `8` | LangGraph recursion limit (loop guard) |
| `TOOL_CALL_TIMEOUT_SECONDS` | `15` | Hard timeout per tool execution |

## Known limitations

- **Live LLM providers are wired but not exercised here.** Development and all testing were done with `LLM_PROVIDER=offline`, a deterministic stand-in that still runs the full pipeline (routing, retrieval, SQL scoping, permissions, citations, no-hallucination behavior) without needing an API key — useful for CI and for anyone checking out the repo without credentials on hand. The `anthropic`/`openai`/`gemini` paths in `app/agent/llm.py` are thin, standard LangChain wrappers selected purely by config (`LLM_PROVIDER` + the matching API key in `.env`); switching to a real model needs no code changes, just a key.
- **Conversation memory is turn-level, not entity-level.** Prior turns are replayed into the synthesis prompt (so a real LLM will naturally use them for follow-up phrasing), but the deterministic *classifier* resolves customer references from the current message only — a follow-up like "what about their tickets?" without repeating the customer name relies on the LLM picking that up from history in the prompt, not on the router re-resolving it structurally.
- **Answer prose quality** (fluency, exact wording, the actual "yes this violates policy because X" judgment call) is inherently LLM-dependent. The eval suite asserts everything that's provider-independent and safety-critical (routing, retrieval/citation correctness, SQL/tool scoping, approval gating, no-hallucination behavior); grading free-text answer quality needs a live model.
- **No bundled web UI** — the brief asked for a chat *interface*, delivered here as a clean, documented HTTP/SSE API (curl-able and easy to wire a frontend to) rather than a bundled frontend, to keep the scope focused on the backend/agentic requirements that are the substance of this assignment.
