# Build Status

- **Last updated:** 2026-11-10, end of Session 6
- **Released version:** `campaign_forge` 1.1.0. Sessions 1-6 are committed on `main` and pushed. CI runs on Python 3.10-3.13 (see the gates table). No 2.0 tag yet.
- **Current phase:** M1 (working AI platform) and the Session 4-6 features are implemented and pass every offline gate. The web app runs locally in demo mode; its **public deployment waits for the owner**. The **live acceptance runs have not passed yet** (see below), so `2.0.0b1` is not tagged. Session 7 is next.

## Sessions

| Session | Title | Status |
| --- | --- | --- |
| S0 | Audit and architectural planning | **Done** |
| S1 | Core foundation, providers, defect fixes | **Done**, except the live eval baseline S1-02 (attempted; see "Live runs") |
| S2 | Orchestration engine | **Done** |
| S3 | Agent library wave 1 (completes M1) | **Done**, except the live acceptance S3-05 (attempted; see "Live runs") |
| S4 | Tools and evidence-backed research | **Done** |
| S5 | Memory and persistence | **Done**, except the Postgres store (deferred, ADR-031) |
| S6 | Streamlit app and early demo deployment | **Done**, except the deployment S6-12 (needs the owner's Streamlit account) |
| S7 | FastAPI service | Next |
| S8 | Agent library wave 2 and Evaluation Lab | Not started |
| S9 | Security hardening and live mode | Not started |
| S10 | Release and presentation | Not started |

## Quality gates (measured 2026-11-10, end of Session 6, Python 3.10, openai 2.44.0, streamlit 1.65.0)

| Gate | Result |
| --- | --- |
| `ruff check .` and `ruff format --check .` | Pass |
| `mypy src` (strict, 83 files) | Pass |
| `pytest` (including the Streamlit AppTest suite) | 653 passed, 0 skipped |
| Branch coverage | 97% (floor: 90%) |
| App starts with no keys (`streamlit run app/streamlit_app.py`) | Yes: a simulated `full-campaign` run completed in a real browser |
| Offline eval, v1 pipeline | 10/10 |
| Offline eval, `full-campaign` | 10/10 (138 calls, 57,427 simulated tokens for 10 briefs) |
| Offline eval, `brand-kit` | 10/10 (70 calls) |
| Offline eval, `competitor-report` | 10/10 (50 calls) |
| CI (GitHub Actions, 3.10-3.13) | **Green** at `30da120` (Session 5, run 42826810974), after one failure on 3.13 only was fixed (unclosed sqlite connections; see [SESSION_LOG.md](SESSION_LOG.md)) |

The offline token counts are word-count estimates from the simulated provider, not real usage.

## Live runs (Groq free tier, `qwen/qwen3.8-27b`, 2026-11-10)

Every live run so far failed. Each failure was diagnosed and fixed, and none of the results below is a pass.

| Run | v1 pipeline | `full-campaign` | Cause, and the fix |
| --- | --- | --- | --- |
| 1 | 0/10 | 0/2 | Retired default model (D11); output cap, "Request too large", truncation, timeouts (D12-D15, ADR-028, ADR-030) |
| 2 | 0/3 | Paused after 16 calls; 7 of 9 checks OK so far | v1 answers truncated at 800 tokens, but need about 940; parallel requests starved each other (D16) |
| 3 | 0/2 | Paused, quota exhausted, after 11 calls | The daily quota (200,000 tokens) was used up by the day's runs; the v1 final brief overflows 1,000 tokens (D17, D18, ADR-032) |

**Next live attempt:** after the daily quota refills. Run:

```
python evals/run_eval.py --only baseline-ecommerce,prompt-injection
python evals/run_eval.py --workflow full-campaign --only baseline-ecommerce
```

Record the results here. Tag `2.0.0b1` only if they pass.

## Open defects

| ID | Summary | Status |
| --- | --- | --- |
| D1-D5, D7-D18 | D1-D10: [PROJECT_AUDIT.md](PROJECT_AUDIT.md). D11-D18: [SESSION_LOG.md](SESSION_LOG.md), ADR-028, ADR-030 and ADR-032 | **Fixed**. D16-D18 are fixed in code, and their live confirmation waits for quota. |
| D6 | Brief text in prompts without data framing | **Partially fixed**: every agent frames its inputs, brand profiles included. Hardening follows in S9. |
| - | DNS rebinding between the URL check and the connection (user-supplied URLs) | Documented residual risk ([SECURITY.md](SECURITY.md)) |

## Waiting on the owner

- **Deploy the demo to Streamlit Community Cloud (S6-12).** It needs your Streamlit account connected to GitHub. The steps:
  1. At share.streamlit.io, choose "Create app", then this repository and branch `main`.
  2. Set the main file path to `app/streamlit_app.py`. Streamlit reads `app/requirements.txt`, next to the entry point. Python 3.12 is fine.
  3. Add no secrets: demo mode needs none.
  4. Open the URL, run a simulated `full-campaign`, and send me the URL, so the smoke test and the URL can be recorded here.
- **Postgres/Supabase memory store:** needs a database to test against (ADR-031).
- **Tags:** `2.0.0b1` waits for a passing live acceptance run.

## Next session: starting instructions

1. Read this file, [SESSION_LOG.md](SESSION_LOG.md), [DECISIONS.md](DECISIONS.md) and [TASK_BOARD.md](TASK_BOARD.md).
2. Run `git status`, then the gates: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`, `python evals/run_eval.py --offline`, `python evals/run_eval.py --offline --workflow full-campaign`.
3. If the Groq daily quota has refilled, rerun the live acceptance (see "Live runs") and record the numbers.
4. If the owner has deployed the demo, smoke-test the URL and record it (S6-12).
5. Start Session 7 (FastAPI service). Write its granular tasks into [TASK_BOARD.md](TASK_BOARD.md) first, from [ROADMAP.md](ROADMAP.md) section S7.
