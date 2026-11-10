# Build Status

- **Last updated:** 2026-11-10, end of Session 3
- **Released version:** `campaign_forge` 1.1.0. The Sessions 1-3 work is on the working tree, uncommitted, and has not been released.
- **Current phase:** Milestone **M1 (Working AI platform) is functionally complete.** Its live acceptance run (S3-05) and the `2.0.0b1` tag wait for the owner. Session 4 is next.

## Sessions

| Session | Title | Status |
| --- | --- | --- |
| S0 | Audit and architectural planning | **Done** |
| S1 | Core foundation, providers, defect fixes | **Done**, except the live tasks S1-01 and S1-02 (need quota approval) |
| S2 | Orchestration engine | **Done**, except the `2.0.0a1` tag (needs commits) |
| S3 | Agent library wave 1 (completes M1) | **Done**, except the live `full-campaign` acceptance run and the `2.0.0b1` tag (S3-05) |
| S4 | Tools and evidence-backed research | Next |
| S5 | Memory and persistence | Not started |
| S6 | Streamlit app and early demo deployment | Not started |
| S7 | FastAPI service | Not started |
| S8 | Agent library wave 2 and Evaluation Lab | Not started |
| S9 | Security hardening and live mode | Not started |
| S10 | Release and presentation | Not started |

## Quality gates (measured 2026-11-10, end of Session 3)

| Gate | Python 3.10 (repo venv, openai 2.44.0) | Python 3.11 (fresh resolve, pinned deps) |
| --- | --- | --- |
| `ruff check .` and `ruff format --check .` | Pass (91 files) | - |
| `mypy src` (strict, 62 files) | Pass | Pass |
| `pytest` | 464 passed | 464 passed |
| Branch coverage | 98.1% (floor: 90%) | - |
| Offline eval, v1 pipeline | 10/10 | - |
| Offline eval, `full-campaign` | 10/10 (118 calls, 50,301 simulated tokens for 10 briefs) | 10/10 |
| Offline eval, `ad-pack` / `social-calendar` / `email-sequence` | 10/10 each | - |
| Package build | wheel and sdist OK | - |
| Live eval (any workflow) | **Not measured** (needs approval) | - |

The offline token counts are word-count estimates from the simulated provider, not real usage.

## Open defects

| ID | Summary | Status |
| --- | --- | --- |
| D1-D5, D7-D10 | See [PROJECT_AUDIT.md](PROJECT_AUDIT.md) | **Fixed** (S1-S2) |
| D6 | Brief text in prompts without data framing | **Partially fixed**: every v1 and library agent frames its inputs. Hardening follows in S9. |
| - | Eval live mode crashed (introduced in S2) | **Fixed** (S3) |
| - | Many-channel briefs could never get a plan or ads | **Fixed** (S3); found by the eval corpus |

## Waiting on the owner

- **Live runs:** approval to spend Groq free-tier quota on:
  - S1-02: the live v1 eval, about 40 calls;
  - S3-05: the live `full-campaign` acceptance, about 12-16 calls per brief (suggested `--limit 2`);
  - S1-01 and ADR-025: the provider smoke test, one tiny call.
- **Commits and tags:** nothing has been committed. Sessions 0-3 are on the `main` working tree. `2.0.0a1` (S2) and `2.0.0b1` (S3) can be tagged once commits are authorised.

## Next session: starting instructions

1. Read this file, [SESSION_LOG.md](SESSION_LOG.md), [DECISIONS.md](DECISIONS.md) and [TASK_BOARD.md](TASK_BOARD.md).
2. Run `git status`, then the gates: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`, `python evals/run_eval.py --offline`, `python evals/run_eval.py --offline --workflow full-campaign`.
3. If the owner has approved them, run the live tasks first (S1-01, S1-02, S3-05) and record the measured numbers.
4. Start Session 4 (tools and evidence-backed research). Write its granular tasks into [TASK_BOARD.md](TASK_BOARD.md) first, from [ROADMAP.md](ROADMAP.md) section S4:
   - tool protocol and permissions;
   - evidence store;
   - user sources;
   - safe URL fetcher with SSRF guard;
   - Wikipedia adapter;
   - optional Tavily adapter;
   - evidence mode for the research agents;
   - `competitor_research` and `fact_checker`;
   - the `competitor-report` template.
