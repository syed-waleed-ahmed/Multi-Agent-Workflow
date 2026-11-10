# Task Board

Granular tasks for each session.

- **Status markers:**
  - `[x]` done (verified)
  - `[ ]` to do
  - `[~]` in progress
  - `[-]` dropped (the reason is recorded in [SESSION_LOG.md](SESSION_LOG.md))
- **Level of detail:** tasks for the next two sessions are detailed. Later sessions are refined at their start, against the actual repository state.

## Session 0 - Audit and architectural planning

- [x] S0-01 Inspect the full source tree, tests, evals, CI, docs and packaging
- [x] S0-02 Measure the baseline: lint, format, types, tests, coverage, offline eval, build, CLI
- [x] S0-03 Reproduce each suspected defect with an offline probe (D1-D7 reproduced; D8 recorded as a risk; D9 found by code reading)
- [x] S0-04 Validate the master prompt's list of 14 known weaknesses against the code
- [x] S0-05 Verify the free-tier terms of LLM providers, hosting and research sources (official sources, dated)
- [x] S0-06 Design the target architecture (ARCHITECTURE.md Part II, with Mermaid diagrams)
- [x] S0-07 Write the ADRs, the agent catalog, the security model, the tech stack, the free-tier policy and the roadmap
- [x] S0-08 Create the progress-tracking files (BUILD_STATUS, SESSION_LOG, TASK_BOARD)
- [x] S0-09 Owner review: the plan and ADR-012, 013, 018 and 019 were approved (instruction to continue), 2026-11-10

## Session 1 - Core foundation, providers, defect fixes

### Baseline

- [~] S1-01 Provider facts were verified on 2026-11-10 (Session 0, same day). Resolving model aliases from live `/models` needs keys and owner approval: run `python scripts/provider_smoke.py --live --models`
- [ ] S1-02 Run a live eval baseline on Groq; record the date, model, pass rate, duration and any 429s. **Blocked: waiting for owner approval to spend Groq quota.**

### Defect fixes, each with a regression test

- [x] S1-03 D1: validate CLI overrides (re-validate `Settings`; argparse range checks); map invalid values to exit code 2
- [x] S1-04 D2: build `Settings` explicitly for long-lived contexts; keep cached `get_settings()` for CLI compatibility; document both
- [x] S1-05 D3: treat an empty Art Director result as a step failure, with one repair attempt
- [x] S1-06 D4: check the required sections of the Manager output, with one repair attempt
- [x] S1-07 D5: guard the `on_result` callback in `run_batch`; correct its docstring
- [x] S1-08 D7: strengthen the eval grounding check (ignore stop-words; use the longest distinctive token)
- [x] S1-09 D8: per-agent `max_tokens`

### Domain and core

- [x] S1-10 `domain/`: `Artifact`, `Claim`, `Provenance`, `Evidence`, `Usage`, `Scope` (Pydantic, frozen where appropriate)
- [x] S1-11 `core/agent.py`: `AgentSpec`, `Agent` protocol, `AgentOutput`
- [x] S1-12 `core/registry.py`: register, lookup, list, enable/disable; entry-point group `campaign_forge.agents`; duplicate-ID protection
- [x] S1-13 `core/budget.py`: thread-safe `Budget` (calls, tokens, wall time, revisions, repairs) with a clear exhaustion error
- [x] S1-14 `core/context.py` and `core/events.py`: `RunContext` and typed events

### Providers

- [x] S1-15 `providers/base.py`: `Provider` protocol, request and response models, normalised errors
- [x] S1-16 `providers/openai_compat.py`: refactor of `LLMClient` transport; usage capture; quota-versus-rate-limit classification; multi-message support
- [x] S1-17 `providers/profiles.py`: Groq, Gemini, Mistral, Cloudflare (data only; `free_tier` flag; data-use notice)
- [x] S1-18 `providers/fake.py`: deterministic `FakeProvider` (routing by agent ID; scripted failures)
- [x] S1-19 `providers/router.py`: configuration precedence, `FREE_ONLY` guard, explicit fallback recorded on output
- [x] S1-20 Keep `LLMClient` as a facade over the new provider; all v1 LLM tests pass unchanged
- [x] S1-21 Provider contract test suite (offline), plus a documented manual live smoke script

### Security primitives

- [x] S1-22 `security/untrusted.py`: framing of untrusted content; apply it to `CampaignBrief.as_prompt_context` consumers (D6, first step)
- [x] S1-23 `security/redaction.py`: log filter that redacts key patterns and configured key values
- [x] S1-24 Architecture test: downward-only imports; no `eval`, `exec` or `subprocess` in the package

### Wrap-up

- [x] S1-25 Add Python 3.13 to the CI matrix
- [x] S1-26 Update the docs (ARCHITECTURE status markers, CHANGELOG `Unreleased`, BUILD_STATUS, SESSION_LOG)

## Session 2 - Orchestration engine

- [x] S2-01 `WorkflowDefinition` model and JSON (de)serialisation
- [x] S2-02 Validator: agents registered and enabled, acyclic graph, satisfiable inputs, tool allowlists, budget ceilings
- [x] S2-03 Run and step state models with the state machine and allowed transitions
- [x] S2-04 Engine core: ready-set scheduling, parallel dispatch, failure isolation, partial results
- [x] S2-05 Declarative `when` conditions (no `eval`)
- [x] S2-06 Bounded review loop (critic hook) and repair loop
- [x] S2-07 Approval gates: `WAITING_FOR_APPROVAL` and the approve/reject/edit round trip
- [x] S2-08 Cooperative cancellation and per-step timeouts
- [x] S2-09 Budget enforcement across parallel steps
- [x] S2-10 Checkpoint stores (in-memory, JSON file); idempotent resume by step ID and input fingerprint
- [x] S2-11 Pause on quota exhaustion with a `resume_after` hint
- [x] S2-12 Rule-based planner; optional constrained LLM planner with validation and fallback
- [x] S2-13 Wrap the v1 agents as registry agents (`legacy.*`); `classic-campaign` template
- [x] S2-14 Rebuild `CampaignWorkflow.run` and `run_batch` on the engine, with incremental batch saves (D9)
- [x] S2-15 CLI: `agents`, `workflows`, `execute`, `runs show|resume|approve|cancel`
- [~] S2-16 Tests for every engine rule (done; coverage 98%). Tagging `2.0.0a1` waits for commit authorisation
- [x] S2-17 Evaluated `openai` 3.49.0: the suite, mypy and the offline SDK error mapping pass. The pin is kept until a live smoke test (ADR-025); the tests are now transport-agnostic

## Session 3 - Agent library, wave 1

- [x] S3-01 `campaign_forge.evaluation`: deterministic checks, rubrics, and an offline responder (shared by the critic, evals, tests and the CLI demo)
- [x] S3-02 Eleven agents per [AGENT_CATALOG.md](AGENT_CATALOG.md), each with spec, prompt version, output contract, validation, tests and rubric
- [x] S3-03 Templates: `full-campaign`, `ad-pack`, `social-calendar`, `email-sequence`
- [x] S3-04 Eval harness `--workflow` mode (per-output deterministic checks, call and token metrics), with a CI step for `full-campaign`
- [ ] S3-05 Live `full-campaign` run on the Groq free tier; record the measured usage; tag `2.0.0b1`. **Blocked: needs approval to spend quota and to commit.**

## Sessions 4-10

Scope and acceptance criteria are in [ROADMAP.md](ROADMAP.md). Granular tasks are written at the start of each session.
