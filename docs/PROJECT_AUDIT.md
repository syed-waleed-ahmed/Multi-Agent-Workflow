# Project Audit - Campaign Forge v1.1.0

- **Audit date:** 2026-11-10 (Session 0)
- **Commit audited:** `808e7d5` on `main` (clean working tree)
- **Purpose:** establish a factual baseline before the Campaign Forge 2.0 work begins. The audit records what exists, what works, which defects exist (each reproduced, not inferred), and what is worth keeping.

Related documents: [ARCHITECTURE.md](ARCHITECTURE.md) (current and target design),
[ROADMAP.md](ROADMAP.md), [DECISIONS.md](DECISIONS.md), [BUILD_STATUS.md](BUILD_STATUS.md).

---

## 1. Baseline measurements

Every number below was measured during this session on Windows 11 using the repository venv (Python 3.10.11).

| Gate | Command | Result |
| --- | --- | --- |
| Lint | `ruff check .` | Pass (no findings) |
| Format | `ruff format --check .` | Pass (29 files) |
| Types | `mypy src` (strict) | Pass (17 source files) |
| Tests | `pytest` | 82 passed in ~1.2 s, fully offline |
| Coverage | `pytest --cov=campaign_forge` | 94% (639 statements, 124 branches) |
| Offline eval | `python evals/run_eval.py --offline` | 10/10 passed |
| Packaging | `python -m build` | wheel + sdist built (`campaign_forge-1.1.0`) |
| CLI | `campaign-forge --version`, `python -m campaign_forge --help` | Work |

**Not measured:** the live-model eval pass rate. A live run would consume the owner's Groq free-tier quota, so it was not done without approval. Until a live run happens, real-model quality is **unknown**, not "passing". This is the first task of Session 1 (see [TASK_BOARD.md](TASK_BOARD.md)).

Installed runtime dependencies: `openai 2.44.0`, `pydantic 2.13.4`, `pydantic-settings 2.14.2`, `tenacity 9.1.4`, `rich 15.0.0`; `httpx 0.28.1` is present transitively through `openai`.

Size: 1,491 lines of Python in `src/` (17 modules), 1,006 lines of tests (8 test modules plus `conftest.py`), and an eval corpus of 10 briefs.

---

## 2. Inventory

| Area | Files | Role |
| --- | --- | --- |
| Domain models | `models.py` | `CampaignBrief` (frozen, length-bounded), `CopyContent`, `CampaignResult` (+ Markdown/JSON export, collision-safe save) |
| LLM transport | `llm.py` | `LLMClient`: OpenAI-compatible chat call, transient-only retries, `Retry-After` handling, empty and truncated response detection |
| Agents | `agents/base.py`, `research.py`, `copywriter.py`, `art_director.py`, `manager.py` | `BaseAgent` (enforces `name` + `system_prompt`), four fixed roles |
| Orchestration | `workflow.py` | `CampaignWorkflow.run()` (hard-coded 4-step chain), `run_batch()` (thread pool, per-item isolation) |
| Parsing | `parsing.py` | Lenient JSON and list recovery from model text |
| Config | `config.py` | `Settings` (pydantic-settings, `CF_` prefix, lazy API key), cached `get_settings()` |
| Interfaces | `cli.py`, `__main__.py`, `main.py` | argparse CLI: `run`, `batch`, `--json`, exit codes 0/1/2/130 |
| I/O | `loaders.py` | Load and validate briefs from JSON |
| Cross-cutting | `exceptions.py`, `logging_config.py` | Typed error hierarchy; Rich logging to stderr on a private namespace |
| Quality | `tests/`, `evals/`, `.github/workflows/ci.yml`, `.pre-commit-config.yaml` | Offline suite, adversarial eval corpus, CI on Python 3.10-3.12 |
| Docs | `README.md`, `docs/ARCHITECTURE.md`, `CONTRIBUTING.md`, `CHANGELOG.md` | Accurate for v1.1 (checked against the code) |

---

## 3. Reusable architecture (keep and build on)

These parts are sound and should carry into 2.0 rather than be rewritten:

1. **Validated boundary models.** Frozen, length-bounded `CampaignBrief` with tolerant coercion (`channels` as list or CSV). This pattern extends to every new artifact type.
2. **Transport resilience in `LLMClient`.** Transient-only retries, `Retry-After` from header or message, a fresh retry controller per call (thread-safe), and fail-loud truncation. This becomes the core of the OpenAI-compatible provider.
3. **Lenient parsing helpers** (`parsing.py`). Reused by structured-output handling.
4. **Agent contract enforcement** (`BaseAgent.__init_subclass__`). Evolves into a declarative `AgentSpec` checked at registration time.
5. **Per-item isolation** in `run_batch()`. The same idea applies to steps inside a workflow.
6. **Collision-safe, atomic export** (`CampaignResult.save()`). Moves into an exports module unchanged.
7. **Typed exception hierarchy.** Extended, not replaced.
8. **Logging discipline.** Logs go to stderr on a private namespace, and the root logger is never touched.
9. **Testing approach.** A duck-typed fake LLM, no network in CI, and an adversarial eval corpus. Generalised into a `FakeProvider` and per-agent eval datasets.
10. **CI gates.** Kept, and extended with security and architecture checks.

---

## 4. Defects (each reproduced in Session 0)

The probes ran offline against a fake LLM. Severity reflects impact on the 2.0 goals, not on the v1.1 CLI alone.

| ID | Defect | Evidence | Severity | Fix in |
| --- | --- | --- | --- | --- |
| D1 | CLI overrides bypass `Settings` validation. `_resolve_settings()` uses `model_copy(update=...)`, which does not validate. | `--workers 500` is accepted, although the Settings field caps it at 64. `--workers -1` crashes `main()` with an uncaught `ValueError: max_workers must be greater than 0` (a raw traceback, not exit code 2). | Medium | S1 |
| D2 | `get_settings()` is `lru_cache`d, so environment changes after the first read are ignored. | Setting `CF_MODEL=model-b` after a first read still returned `model-a`. Harmless for a one-shot CLI, wrong for a long-lived Streamlit or API process that needs per-session settings. | Medium (for 2.0) | S1 |
| D3 | Art Director can return zero prompts and the run still "succeeds". | `{"image_prompts": []}` gives `image_prompts=[]` and a successful `CampaignResult`; only a log warning is emitted, and the Manager receives "None". | Medium | S1 (guard), S3 (agent rewrite) |
| D4 | Manager output is not validated. | A Manager response of `"ok"` produced a successful run with `final_brief='ok'`. The six required sections are never checked. | Medium | S1 (section check), S3 (critic) |
| D5 | A raising `on_result` callback aborts the whole batch, and its docstring is wrong. | The exception propagated out of `run_batch()` and discarded completed results. The callback runs on the **calling** thread (inside the `as_completed` loop), not on worker threads as the docstring says. | Low | S1 |
| D6 | Untrusted brief text goes into prompts verbatim, with no data delimiting. | `as_prompt_context()` embeds `"IGNORE ALL PREVIOUS INSTRUCTIONS."` unchanged inside the instruction text. | High (for a public demo) | S1 (primitives), S9 (hardening) |
| D7 | The eval grounding check is weak. | `_grounded("The Bottle", ...)` passes on the stop-word "the". The injection check is a two-keyword heuristic (`banana`, `system prompt`). | Low | S1 (grounding), S8 (evals) |
| D8 | Risk, not reproduced: a truncation at the final step loses the whole campaign. | The default `CF_MAX_TOKENS=800` is shared by all agents. The Manager writes the longest output last; a `finish_reason == "length"` there raises `LLMError` after three successful paid-for calls, with nothing saved. | Medium | S1 (per-agent limits), S2 (checkpoints) |
| D9 | Batch results are written only after the whole batch finishes. | `cmd_batch()` saves results after `run_batch()` returns, so an interrupt or crash loses every completed campaign. | Low | S2 |

The probe script is not part of the repository. Each defect gets a regression test when it is fixed (see [TASK_BOARD.md](TASK_BOARD.md)).

---

## 5. Validation of previously reported weaknesses

The master prompt asked for every known weakness to be re-checked against the code, not assumed.

| Reported weakness | Status | Notes |
| --- | --- | --- |
| Research agent does no genuine external research | **Confirmed** | `ResearchAgent` sends the brief to the model and returns text. There are no tools or sources, and its claims about "trends" and "competitor angles" are unlabeled model assumptions. |
| No critic or reviewer workflow | **Confirmed** | The only correction loop is the copywriter's one-shot JSON repair, and it checks syntax, not quality. |
| No persistent campaign memory | **Confirmed** | Results are written to files and never read back. |
| No caching | **Confirmed** | Identical requests always call the provider again. |
| No token or cost tracking | **Confirmed** | `llm.py` discards `response.usage`, although every OpenAI-compatible provider returns it. |
| No proper observability | **Partial** | Leveled logs exist, but there are no run or step IDs, metrics, events or tracing. |
| No custom workflow builder | **Confirmed** | The pipeline is hard-coded in `CampaignWorkflow.__init__` and `run()`. |
| No agent registration system | **Confirmed** | Agents are instantiated directly, and `CONTRIBUTING.md` says to "wire it into `CampaignWorkflow`". |
| No per-agent provider configuration | **Confirmed** | One `LLMClient` with one model and one temperature serves all agents. |
| Weak prompt-injection defenses | **Confirmed** | See D6 and D7. |
| Manager output insufficiently validated | **Confirmed** | See D4. |
| Art Director can return an empty result | **Confirmed** | See D3. |
| CLI worker overrides may bypass validation | **Confirmed, and worse than reported** | See D1: negative values crash with a raw traceback. |
| Cached settings ignore later environment changes | **Confirmed** | See D2. This is by design for the CLI, but it must change for servers and the UI. |

---

## 6. Technical debt that blocks 2.0

These are not bugs. They are v1.1 design choices that the 2.0 goals cannot be built on:

1. **Positional agent signatures** (`run(brief, research, copy, prompts)`). Every new agent would change the orchestrator. 2.0 needs typed artifacts passed by name.
2. **Hard-coded pipeline.** Workflows need to be data (definitions), not code.
3. **Prompts as unversioned string constants.** Eval comparisons need a prompt version on each agent spec.
4. **A single global model configuration.** Free-tier economics need small models for simple agents and stronger ones for synthesis.
5. **No run state.** Without step-level state there can be no resume, approval, cancellation or partial results.
6. **Single-turn transport only** (system + user). A repair loop with conversation history, or a revision with critic feedback, needs a list of messages.

---

## 7. Strengths worth showcasing in the 2.0 presentation

- The "fail loudly on truncation" and "retry only transient errors" choices are uncommon in hobby LLM projects and are worth keeping prominent.
- The adversarial eval corpus (non-Latin input, emoji, an injection attempt, oversized input) is a good foundation for the 2.0 Evaluation Lab.
- The dependency-light core (5 runtime dependencies) is a credibility point. The 2.0 dependency policy ([ADR-007](DECISIONS.md)) keeps the core light and puts the UI, API and DB behind optional extras.

---

## 8. Audit conclusion

The v1.1 codebase is a healthy base: every gate is green, the documentation matches the code, and there is no critical defect for its current CLI use. The one high-severity item, D6, matters only once untrusted visitors can submit briefs (the public demo). The 2.0 effort is therefore an **evolution, not a rewrite** ([ADR-001](DECISIONS.md)). The v1 public API and the 82 existing tests are preserved as the regression suite while the engine underneath is generalised.
