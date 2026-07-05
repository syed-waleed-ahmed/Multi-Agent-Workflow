# Evals

The unit-test suite (`tests/`) is fully mocked — it proves the *plumbing* works
but never asks a real model whether a weird, huge, non-English, or adversarial
brief actually produces a usable campaign. This directory closes that gap.

- **`corpus.json`** — a deliberately diverse set of briefs: normal cases,
  minimal input, non-Latin and emoji text, an oversized description, a long
  channel list, a prompt-injection attempt, and an ambiguous brief. Each entry
  has an `id`, a `category`, and a `notes` field explaining what it stresses.
- **`run_eval.py`** — runs every brief end-to-end through `CampaignWorkflow` and
  scores each result on **critical** checks (completed without error, brief
  references the product, brief is substantial, image prompts and headlines
  present, injection resisted) plus non-fatal **quality warnings** (3–5 headlines
  / image prompts, as the prompts request).

## Running

Real model (needs `GROQ_API_KEY` or `OPENAI_API_KEY` in the env or `.env`):

```bash
python evals/run_eval.py                       # scorecard for the whole corpus
python evals/run_eval.py --report report.json  # also write a machine-readable report
python evals/run_eval.py --min-pass-rate 0.9   # exit non-zero below 90% (CI gate)
```

Offline (no key, deterministic fake model — verifies the harness and gives a
green baseline):

```bash
python evals/run_eval.py --offline
```

The process exits non-zero when the pass rate drops below `--min-pass-rate`
(default `0.8`), so it can run as a nightly or pre-release gate. Add new briefs
to `corpus.json` whenever you find an input shape the pipeline should handle.
