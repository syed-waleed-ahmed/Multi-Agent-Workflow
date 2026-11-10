# Technology Stack

This document lists the current stack and the planned 2.0 stack. It also justifies
every new dependency before it is added, as the master prompt requires.

Dependency policy (ADR-007):

- The **core package stays light.** Everything heavy sits behind an optional extra.
- **Each new dependency** is approved here and recorded in [DECISIONS.md](DECISIONS.md).
  The record states why the dependency is needed, its licence, whether an existing
  dependency could do the job, and what it costs to maintain.

## 1. Current stack (v1.1.0, measured 2026-11-10)

| Layer | Technology | Installed version | Licence |
| --- | --- | --- | --- |
| Language | Python | 3.10.11 (CI: 3.10, 3.11, 3.12) | PSF |
| LLM transport | `openai` SDK, used as a generic OpenAI-compatible client | 2.44.0 | Apache-2.0 |
| Validation and models | `pydantic` | 2.13.4 | MIT |
| Configuration | `pydantic-settings` | 2.14.2 | MIT |
| Retries | `tenacity` | 9.1.4 | Apache-2.0 |
| Terminal output and logging | `rich` | 15.0.0 | MIT |
| HTTP (transitive, via `openai`) | `httpx` | 0.28.1 | BSD-3-Clause |
| Lint and format (dev) | `ruff` | - | MIT |
| Types (dev) | `mypy --strict` + the pydantic plugin | - | MIT |
| Tests (dev) | `pytest`, `pytest-cov` | - | MIT |
| CI | GitHub Actions | - | free for public repositories |
| Packaging | setuptools, `src/` layout, single-sourced version | - | MIT |

## 2. Target stack (2.0)

| Concern | Choice | Extra | Introduced | Status |
| --- | --- | --- | --- | --- |
| LLM providers | One `OpenAICompatibleProvider` (existing `openai` SDK) with vendor profiles: Groq, Gemini, Mistral, Cloudflare Workers AI | core | S1 | Planned |
| Deterministic test and demo provider | `FakeProvider` (own code) | core | S1 | Planned |
| Orchestration | Own engine: declarative DAG, thread pool, budgets | core | S2 | Planned |
| Workflow definitions | JSON (stdlib) + Python templates | core | S2 | Planned |
| Checkpoints (zero-dependency) | JSON files (stdlib) | core | S2 | Planned |
| HTTP for tools | `httpx` (already installed; to be declared explicitly) | core | S4 | Planned |
| HTML to text | stdlib `html.parser` first; `trafilatura` only if quality demands it | `[research]` | S4 | Planned |
| `robots.txt` | stdlib `urllib.robotparser` | core | S4 | Planned |
| Persistence | SQLite (stdlib `sqlite3` or SQLAlchemy Core; decided in S5) | core / `[db]` | S5 | Decision pending |
| Optional cloud database | Supabase Postgres through a Postgres driver | `[db]` | S5 | Optional |
| Full-text search | SQLite FTS5 / Postgres full-text | - | S5 | Planned |
| User interface | Streamlit | `[ui]` | S6 | Planned |
| Workflow visualisation | `st.graphviz_chart` (rendered in the browser, so no system Graphviz needed) | `[ui]` | S6 | Planned |
| API | FastAPI + Uvicorn | `[api]` | S7 | Planned |
| Container | Docker (development and self-hosting only) | - | S7 | Planned |
| Tracing | OpenTelemetry SDK, console or OTLP exporter | `[otel]` | S9 | Optional |
| Dependency audit | `pip-audit` (in CI) | dev | S9 | Planned |
| Architecture rules | Own test using stdlib `ast` | dev | S1 | Planned |

Python floor: **3.10** stays (ADR-016). Streamlit Community Cloud defaults to 3.12 and
supports every Python version that still receives security updates. Session 1 adds
3.13 to the CI matrix.

## 3. Dependency justifications

| Dependency | Why it is needed | Licence | Could an existing dependency do it? | Maintenance |
| --- | --- | --- | --- | --- |
| `httpx` (made explicit) | Bounded, timeout-controlled fetching for research tools | BSD-3-Clause | It already ships with `openai`; declaring it only pins the contract | None new |
| `streamlit` | The public demo UI with the least front-end code, plus free hosting | Apache-2.0 | No | Large tree but well maintained; isolated in `[ui]` |
| `fastapi` + `uvicorn` | Typed REST API that reuses the Pydantic models | MIT / BSD-3-Clause | The stdlib `http.server` is not suitable for production | Popular, stable; isolated in `[api]` |
| `sqlalchemy` (if chosen in S5) | One code path for SQLite and Postgres | MIT | Stdlib `sqlite3` covers SQLite only and would mean duplicate SQL for Postgres | Mature; isolated in `[db]` |
| Postgres driver (`psycopg`) | Supabase connectivity | LGPL-3.0 | No | Optional, self-hosters only |
| `trafilatura` (only if needed) | Better main-content extraction than a simple HTML parser | Apache-2.0 | Partly: the stdlib `html.parser` handles simple pages | Added only if S4 evals show a quality gap |
| `opentelemetry-sdk` | Standard tracing for operators who want it | Apache-2.0 | Logs and events cover the basics | Optional |
| `pip-audit` (dev) | Known-vulnerability scan in CI | Apache-2.0 | No | Dev only |

## 4. Considered and rejected

| Option | Reason |
| --- | --- |
| LangChain / LangGraph / CrewAI / AutoGen | All free and open source, but each brings a large dependency tree and frequent API churn. The engine Campaign Forge needs (a bounded DAG with typed artifacts) is small, and owning it is part of the portfolio value. Revisit only if a needed feature would cost more than a week to build. |
| Native vendor SDKs (`google-genai`, `mistralai`, ...) | Every target vendor exposes an OpenAI-compatible endpoint. A native SDK is added only when a needed feature is missing there (ADR-003). |
| PyYAML for workflow files | JSON is in the stdlib and enough for machine-edited definitions (ADR-015) |
| Vector databases (Chroma, Qdrant, pgvector as a requirement) | Not needed for the first memory version. Full-text search comes first, and semantic search stays optional (master prompt rule). |
| asyncio rewrite | The work is I/O-bound, the sync client is thread-safe, and Streamlit is synchronous. Threads keep the code simpler (ADR-009). |
| Hugging Face Spaces, Cloudflare Workers for hosting | No longer free for Docker or Streamlit (HF), or a 10 ms CPU limit (Cloudflare). See [FREE_TIER_POLICY.md](FREE_TIER_POLICY.md). |
| Brave Search, Google Custom Search, Bing, DuckDuckGo scraping | Need a card, closed to new customers, retired, or unclear terms ([FREE_TIER_POLICY.md](FREE_TIER_POLICY.md)) |

## 5. Packaging plan

```text
pip install campaign-forge              # core: library + CLI (current 5 deps + httpx)
pip install "campaign-forge[ui]"        # + Streamlit app
pip install "campaign-forge[api]"       # + FastAPI service
pip install "campaign-forge[db]"        # + SQLAlchemy / Postgres driver (if chosen)
pip install "campaign-forge[research]"  # + optional extraction helpers
pip install "campaign-forge[all]"       # everything
pip install -e ".[dev]"                 # contributors
```

Streamlit Community Cloud installs from a `requirements.txt`. The exact way to install
the local package with the `[ui]` extra there is verified in Session 6 before the first
deployment.
