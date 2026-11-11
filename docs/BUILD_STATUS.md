# Build Status

- **Last updated:** 2026-11-11, end of Session 9
- **Version:** **`2.0.0rc1`** (release candidate), tagged **`v2.0.0rc1`** at `4c8b726` (tag confirmed on GitHub 2026-11-11). The previous pre-release is tagged `v2.0.0b1` (`94af144`). No GitHub release pages exist yet; they are left to the owner. Session 9 is on `main` after the tag (unreleased in `CHANGELOG.md`). CI is green on Python 3.10-3.13, the Docker job and the dependency audit.
- **Current phase:** The full agent catalog (28 agents, 14 templates), memory, the web app, the API and the Evaluation Lab are implemented and pass every offline gate. **The live acceptance runs passed on 2026-11-11** (v1 2/2, `full-campaign` 1/1 with no warnings). The web app's demo mode is **live at https://campaign-forge.streamlit.app/** (deployed and smoke-tested 2026-11-11). Session 9 added live mode and security hardening (ADR-037). The public app offers bring-your-own-key live runs. Offering the demo's own key needs the owner's Streamlit secrets. Session 10 (release and presentation) is next.

## Sessions

| Session | Title | Status |
| --- | --- | --- |
| S0 | Audit and architectural planning | **Done** |
| S1 | Core foundation, providers, defect fixes | **Done**; live eval baseline passed 2026-11-11 (run 5) |
| S2 | Orchestration engine | **Done** |
| S3 | Agent library wave 1 (completes M1) | **Done**; live `full-campaign` acceptance passed 2026-11-11 (run 5) |
| S4 | Tools and evidence-backed research | **Done** |
| S5 | Memory and persistence | **Done**, except the Postgres store (deferred, ADR-031) |
| S6 | Streamlit app and early demo deployment | **Done**; demo live at https://campaign-forge.streamlit.app/ |
| S7 | FastAPI service | **Done** (Docker verified in CI) |
| S8 | Agent library wave 2 and Evaluation Lab | **Done**: 13 agents, 7 templates, Lab, benchmarks from real runs ([BENCHMARKS.md](BENCHMARKS.md)) |
| S9 | Security hardening and live mode | **Done** (ADR-037). A live run on the owner's key passed locally. On the public app it waits for the owner's secrets. |
| S10 | Release and presentation | Next |

## Quality gates (measured 2026-11-11, end of Session 9, Python 3.10, streamlit 1.65.0; the UI suites and `mypy` also on Python 3.11 with streamlit 1.67.0)

| Gate | Result |
| --- | --- |
| `ruff check .` and `ruff format --check .` | Pass |
| `mypy src` (strict, 104 files) | Pass |
| `pytest` (including the Streamlit AppTest suite and the live-mode tests) | 792 passed, 0 skipped |
| Branch coverage | 97% (floor: 90%) |
| Offline benchmarks against their baselines | `full-campaign` corpus 10/10, injection 14/14; `seo-content-pack` corpus 10/10 |
| `pip-audit` (CI, pinned runtime dependencies of every extra) | No known vulnerabilities found (2026-11-11) |
| Live mode in a real browser (local, owner's key) | `ad-pack` run on Groq: 4/4 steps, 11 calls, 16,902 tokens, inside the caps; the plan showed 30,000 tokens (the template allows 60,000); an invalid key was refused, the field cleared, and the server log held no key-shaped string |
| App starts with no keys (`streamlit run app/streamlit_app.py`) | Yes: a simulated `full-campaign` run completed in a real browser |
| Public demo (https://campaign-forge.streamlit.app/) | **Live.** Smoke test on 2026-11-11: the dashboard, the New Run plan preview, then "Approve and run": a `full-campaign` run completed (14 calls, 11/11 steps), with the SIMULATED notice, run graph, report, deliverables, sources and downloads shown. Streamlit Community Cloud, Python 3.13, branch `main`, no secrets. |
| Offline eval, v1 pipeline | 10/10 |
| Offline eval, `full-campaign` | 10/10 (138 calls, 57,507 simulated tokens for 10 briefs) |
| Offline eval, `brand-kit` | 10/10 (70 calls) |
| Offline eval, `competitor-report` | 10/10 (50 calls) |
| CI (GitHub Actions, 3.10-3.13, Docker, dependency audit) | **Green** at `1d42c3f` (Session 9, run 43048926866) |
| Public demo after the Session 9 push | Rebooted. The dashboard links to live mode, and Settings offers the bring-your-own-key form (no owner key is configured). A simulated `full-campaign` run completed, with its report and deliverables. |

The offline token counts are word-count estimates from the simulated provider, not real usage.

## Live runs (Groq free tier: `qwen/qwen3.8-27b`, plus `openai/gpt-oss-120b` for long answers)

Runs 1-3 failed (2026-11-10). Each failure was diagnosed and fixed. **Run 5 is the first full pass.**

| Run | v1 pipeline | `full-campaign` | Cause, and the fix |
| --- | --- | --- | --- |
| 1 | 0/10 | 0/2 | Retired default model (D11); output cap, "Request too large", truncation, timeouts (D12-D15, ADR-028, ADR-030) |
| 2 | 0/3 | Paused after 16 calls; 7 of 9 checks OK so far | v1 answers truncated at 800 tokens, but need about 940; parallel requests starved each other (D16) |
| 3 | 0/2 | Paused, quota exhausted, after 11 calls | The daily quota (200,000 tokens) was used up by the day's runs; the v1 final brief overflows 1,000 tokens (D17, D18, ADR-032) |
| 4 (2026-11-11) | 1/2 (`prompt-injection` passed) | **Passed**: 21 calls, 26,535 tokens, 622 s; 2 warnings | v1 research and the optional social calendar truncated at qwen's 1,000-token cap; ads review 0.69 (D19, ADR-035) |
| **5** (2026-11-11) | **2/2 passed** (26 s, 33 s) | **Passed, no warnings**: 19 calls, 27,129 tokens, 375 s | All checks green, including the social calendar and the ads review |

To repeat it:

```
python evals/run_eval.py --only baseline-ecommerce,prompt-injection
python evals/run_eval.py --workflow full-campaign --only baseline-ecommerce
```

A run uses about 27,000 tokens of the 200,000-token daily quota. The v1 harness does not report token usage (0 in its summary): the v1 facade returns results, not usage. Run 5 also had one recovered truncation (`audience_research`) and 17 rate-limit retries, which are normal on this tier.

## Open defects

| ID | Summary | Status |
| --- | --- | --- |
| D1-D5, D7-D20 | D1-D10: [PROJECT_AUDIT.md](PROJECT_AUDIT.md). D11-D20: [SESSION_LOG.md](SESSION_LOG.md), ADR-028, ADR-030, ADR-032 and ADR-035 | **Fixed**. D11-D19 are confirmed by live run 5, and D20 by the live benchmark reruns. |
| D6 | Brief text in prompts without data framing | **Fixed** (S1, S9): every agent frames its inputs; the framing removes hidden characters, marker phrases and chat tokens; a 14-case injection dataset passes offline in CI. No defence makes a model immune ([SECURITY.md](SECURITY.md) section 4). |
| - | DNS rebinding between the URL check and the connection (user-supplied URLs) | Documented residual risk ([SECURITY.md](SECURITY.md)) |

## Waiting on the owner

- **Offer the demo's key on the public app (S9 acceptance on the deployed app).**
  1. In Streamlit Community Cloud, open the app, then Settings, then Secrets.
  2. Add `GROQ_API_KEY = "gsk_..."` and `CF_LIVE_OWNER_KEY = "true"`. The value must be a string: Streamlit exports only string and number secrets.
  3. Reboot the app.

  The caps of [SECURITY.md](SECURITY.md) section 5 then apply. The next session verifies a live run in the browser.
- **GitHub release pages for `v2.0.0b1` and `v2.0.0rc1`.** Both tags are pushed; the pages were blocked by the session's permission policy (they create public surfaces).
- **Postgres/Supabase memory store:** needs a database to test against (ADR-031).

## Next session: starting instructions

1. Read this file, [SESSION_LOG.md](SESSION_LOG.md), [DECISIONS.md](DECISIONS.md) and [TASK_BOARD.md](TASK_BOARD.md).
2. Run `git status`, then the gates: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`, `python evals/run_eval.py --offline`, `python evals/run_eval.py --offline --workflow full-campaign`.
3. If the Groq daily quota has refilled, rerun the live acceptance (see "Live runs") and record the numbers. If the owner has added the Streamlit secrets, run one live workflow on the public app on the demo's key and record it.
4. Check that https://campaign-forge.streamlit.app/ still loads. A push to `main` pulls the new code, but modules imported from `src/` stay cached until the app restarts. After a code push, reboot it (Manage app, then the menu, then Reboot app) and confirm the change is live.
5. Start Session 10 (release and presentation). Write its granular tasks into [TASK_BOARD.md](TASK_BOARD.md) first, from [ROADMAP.md](ROADMAP.md) section S10.
