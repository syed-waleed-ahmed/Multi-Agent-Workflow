# Build Status

- **Last updated:** 2026-11-11, end of Session 10
- **Version:** **`2.0.0`**, committed on `main` as `74dd7f2` with CI green: Python 3.10-3.13, the Docker job and the dependency audit.
  - **Tag `v2.0.0` and the GitHub release:** not created yet. The session's permission policy blocks tags and releases, so they are prepared in the browser for the owner's click (see "Waiting on the owner").
  - **Earlier pre-releases:** tagged `v2.0.0rc1` (`4c8b726`) and `v2.0.0b1` (`94af144`).
- **Current phase:** The full agent catalog (28 agents, 14 templates), memory, the web app, the API and the Evaluation Lab are implemented and pass every offline gate. **The live acceptance runs passed on 2026-11-11** (v1 2/2, `full-campaign` 1/1 with no warnings). The web app's demo mode is **live at https://campaign-forge.streamlit.app/** (deployed and smoke-tested 2026-11-11). Session 9 added live mode and security hardening (ADR-037). Session 10 completed the presentation, the final live smoke test and the 2.0.0 release. **All planned sessions are done.** The definition-of-done audit is below; its one open item needs the owner's Streamlit secrets.

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
| S10 | Release and presentation | **Done**: README, gallery, deployment docs and community files; final live smoke test; planner in the CLI; release `2.0.0` (tag and GitHub release await the owner's click) |

## Quality gates (measured 2026-11-11, end of Session 10, Python 3.10, streamlit 1.65.0; `mypy` also on Python 3.11 with streamlit 1.67.0)

| Gate | Result |
| --- | --- |
| `ruff check .` and `ruff format --check .` | Pass |
| `mypy src` (strict, 104 files) | Pass |
| `pytest` (including the Streamlit AppTest suite and the live-mode tests) | 794 passed, 0 skipped |
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
| CI (GitHub Actions, 3.10-3.13, Docker, dependency audit) | **Green** at `74dd7f2`, the 2.0.0 release commit (run 43054635218) |
| Final live smoke test (Session 10) | `campaign-forge execute full-campaign` on Groq's free tier: completed, every critical check passed, 18 calls, 28,592 tokens, 234 s; one warning (the optional ads step failed a platform limit). The output is in [EXAMPLES.md](EXAMPLES.md). |
| Public demo, 2.0.0 | Rebooted after the release push. It shows "Campaign Forge 2.0.0". A simulated `full-campaign` run completed (11/11 steps, 14 calls), and the live-mode section offers the bring-your-own-key form. The README's six Mermaid diagrams render on GitHub. |

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

## Definition of done (master prompt, sections A-E), audited at 2.0.0

| Criterion | Met? | Evidence |
| --- | --- | --- |
| **A. Core** | | |
| The original package remains functional | Yes | The v1 `run` and `batch` commands and `CampaignWorkflow` run on the 2.0 engine. Their tests pass, and the live v1 eval passed 2/2 (run 5). |
| Agent registration works | Yes | `AgentRegistry` with entry points; 32 agents registered; registry and plugin tests |
| Multiple providers are supported | Yes, with a note | Groq, Gemini, Mistral, Cloudflare and a simulated provider behind one router, all tested offline. Only Groq was exercised live (the free key at hand). |
| Workflows execute correctly | Yes | All 14 templates pass offline. Live: `full-campaign` (three runs), `landing-page`, `seo-content-pack`, `ad-pack` |
| Execution sessions are managed | Yes | Run state machine with checkpoints, pause and resume, approvals, cancellation, and capped background runs |
| Results are typed and validated | Yes | Pydantic contracts for every artifact kind; code-computed numbers; validated citations |
| **B. Agents** | | |
| Specialized agents perform actual tasks | Yes | 28 agents, none a placeholder (AGENT_CATALOG section 1) |
| Planner assigns tasks | Yes | Within a workflow, the engine's conditions choose the steps. `campaign-forge plan` and `execute auto` choose the workflow: live, the planner picked `full-campaign` for a four-channel brief and `email-sequence` for an email-only one. The web app still asks the user to pick (listed in the README roadmap). |
| Critic reviews outputs | Yes | Rubric scoring with bounded revision; review scores recorded per step |
| Agents communicate through structured artifacts | Yes | `Artifact` with claims, evidence and provenance between every step |
| Failed stages can be retried or resumed | Yes | `runs resume` (with `--rerun`), auto-resume on rate limits, and per-step retries |
| **C. User experience** | | |
| The UI works | Yes | The public demo was smoke-tested in a browser after every code push |
| Users can create campaigns | Yes | New Run and seven studios, with plan preview and approval |
| Users can inspect agent execution | Yes | Run Inspector: live graph, steps, usage, outputs, provenance |
| Users can download outputs | Yes | Markdown, JSON and HTML |
| Users can manage brand profiles | Yes | Brand Memory page, `memory brand` CLI |
| Demo mode requires no API key | Yes | Public demo, `CF_PROVIDER=fake` |
| **D. Quality** | | |
| Offline tests pass | Yes | 794 passed, 97% branch coverage |
| Evaluation suite passes | Yes | Offline 10/10 (v1 and `full-campaign`); Lab baselines 10/10, 14/14 (injection), 10/10 |
| Security checks pass | Yes | `tests/security`, no-leak tests for keys, `pip-audit` with no known vulnerabilities |
| CI passes | Yes | Green at `74dd7f2` |
| No known critical defects remain | Yes | D1-D21 are fixed. A live model can still miss a platform limit; the contract refuses that output (a warning, not a defect). |
| **E. Deployment** | | |
| GitHub repository is organized | Yes | README (20 sections, Mermaid diagrams), community files, issue templates, docs index |
| Public demo is deployed | Yes | https://campaign-forge.streamlit.app/ |
| Public demo has been smoke-tested | Yes, with one open item | Demo mode, after every push. Live mode on the public app: the visitor-key form works, but running live on the demo's own key needs the owner's Streamlit secrets. The same owner-key live run passed locally in a browser on 2026-11-11. |
| Deployment instructions are complete | Yes | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Free-tier limitations are documented | Yes | DEPLOYMENT.md, FREE_TIER_POLICY.md, README "Limitations" |

## Open defects

| ID | Summary | Status |
| --- | --- | --- |
| D1-D5, D7-D21 | D1-D10: [PROJECT_AUDIT.md](PROJECT_AUDIT.md). D11-D21: [SESSION_LOG.md](SESSION_LOG.md), ADR-028, ADR-030, ADR-032 and ADR-035 | **Fixed**. D11-D19 are confirmed by live run 5, D20 by the live benchmark reruns, and D21 by a regression test that reproduces it. |
| D6 | Brief text in prompts without data framing | **Fixed** (S1, S9): every agent frames its inputs; the framing removes hidden characters, marker phrases and chat tokens; a 14-case injection dataset passes offline in CI. No defence makes a model immune ([SECURITY.md](SECURITY.md) section 4). |
| - | DNS rebinding between the URL check and the connection (user-supplied URLs) | Documented residual risk ([SECURITY.md](SECURITY.md)) |

## Waiting on the owner

- **Publish the prepared release pages** (`v2.0.0`, which also creates the tag, and the pre-releases `v2.0.0rc1` and `v2.0.0b1`), and the repository's About description and website.
- **Optional:** turn on private vulnerability reporting (Settings, then Code security). [`SECURITY.md`](../SECURITY.md) works either way.
- **Offer the demo's key on the public app (S9 acceptance on the deployed app).**
  1. In Streamlit Community Cloud, open the app, then Settings, then Secrets.
  2. Add `GROQ_API_KEY = "gsk_..."` and `CF_LIVE_OWNER_KEY = "true"`. The value must be a string: Streamlit exports only string and number secrets.
  3. Reboot the app.

  The caps of [SECURITY.md](SECURITY.md) section 5 then apply. The next session verifies a live run in the browser.
- **Postgres/Supabase memory store:** needs a database to test against (ADR-031).

## Next session: starting instructions

1. Read this file, [SESSION_LOG.md](SESSION_LOG.md), [DECISIONS.md](DECISIONS.md) and [TASK_BOARD.md](TASK_BOARD.md).
2. Run `git status`, then the gates: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`, `python evals/run_eval.py --offline`, `python evals/run_eval.py --offline --workflow full-campaign`.
3. If the owner has added the Streamlit secrets, run one live workflow on the public app on the demo's key, record it here and in [BENCHMARKS.md](BENCHMARKS.md), and close the last definition-of-done gap.
4. Check that https://campaign-forge.streamlit.app/ still loads. A push to `main` pulls the new code, but modules imported from `src/` stay cached until the app restarts. After a code push, reboot it (Manage app, then the menu, then Reboot app) and confirm the change is live.
5. All planned sessions are done. New work starts from the README's roadmap section, with its tasks written into [TASK_BOARD.md](TASK_BOARD.md) first.
