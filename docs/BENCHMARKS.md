# Benchmarks

This page lists **measured results from real runs only**. Each entry records:
- the date;
- the provider;
- the models that answered;
- the dataset and cases;
- what was measured.

Simulated runs (the offline responder) are **not benchmarks**. They check the workflows' contracts and are used as regression baselines in CI (`src/campaign_forge/evaluation/baselines/`). They never appear here.

To produce an entry:

```bash
campaign-forge bench <workflow> --only <case ids> --report report.json --markdown report.md
```

Commit the JSON report in [`docs/benchmarks/`](benchmarks/) next to the entry.

## Provider and tier

All runs below used **Groq's free tier**, with no billing attached:
- `qwen/qwen3.8-27b` is the default for the small and standard classes. It allows 1,000 output tokens per request and per minute, and 200,000 tokens per day.
- `openai/gpt-oss-120b` is the strong class. Cap-aware routing (ADR-035) also uses it for long answers.

Durations include waits for free-tier rate limits, so on this tier they measure the provider's limits as much as the workflow. The estimated cost of every run is $0.00.

## Results

| Date (UTC) | Workflow | Dataset and cases | Result | Calls | Tokens | Duration | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-11 15:42 | `full-campaign` | the gallery brief (EcoSip, four channels) | **Pass** (all critical checks), 1 warning | 18 | 28,592 | 233.7 s | Session 10 final smoke test with `campaign-forge execute`; critic review 0.75. Warning: the optional ads step failed, because a Google Ads headline was 37 characters (limit 30) after one repair. [Output](examples/full-campaign-live.md) |
| 2026-11-11 14:19 | `seo-content-pack` | corpus: `baseline-ecommerce` | **Pass** (all checks) | 11 | 11,792 | 226.5 s | Keywords, plan, article (outline and sections), audit, safety review. [Report](benchmarks/2026-11-11-seo-content-pack-live.json) |
| 2026-11-11 14:14 | `landing-page` | corpus: `baseline-ecommerce` | **Pass** (all checks) | 12 | 13,577 | 273.5 s | Critic review score 0.78; 1 retry. [Report](benchmarks/2026-11-11-landing-page-live.json) |
| 2026-11-11 13:11 | `full-campaign` | corpus: `baseline-ecommerce` | **Pass** (all checks, no warnings) | 19 | 27,129 | 374.8 s | Live run 5, with cap-aware routing. One truncated answer was recovered (`audience_research`). |
| 2026-11-11 13:10 | v1 pipeline (`classic-campaign`) | corpus: `baseline-ecommerce`, `prompt-injection` | **2/2 pass** | not reported | not reported | 26.1 s, 33.4 s | Live run 5. The v1 harness does not report usage. |
| 2026-11-11 12:56 | `full-campaign` | corpus: `baseline-ecommerce` | Pass, 2 warnings | 21 | 26,535 | 622.1 s | Live run 4, before cap-aware routing: the optional social calendar was truncated, and the ads review scored 0.69 (D19). |
| 2026-11-11 12:54 | v1 pipeline | corpus: `baseline-ecommerce`, `prompt-injection` | 1/2 | not reported | not reported | 144.6 s | Live run 4: the research answer overflowed qwen's 1,000-token output cap (D19). |

Live runs 1-3 (2026-11-10) failed before completing; [BUILD_STATUS.md](BUILD_STATUS.md) lists their causes.

## What the numbers do and do not show

- **One case per workflow.** These are acceptance runs, not statistically meaningful comparisons. Many cases per workflow would exceed the free tier's 200,000-token daily quota.
- **Pass criteria.** A pass means every critical deterministic check passed on a real model's output: schema, injection canaries, grounding in the brief, labeled claims and verified citations, report sections. Quality beyond those checks is judged by the critic's rubric score (0-1), where one is reported.
- **Models.** Runs before 13:10 on 2026-11-11 used the strong model only for the v1 manager and the campaign reporter (ADR-032). Later runs also use it for any answer longer than qwen's per-request cap (ADR-035).
