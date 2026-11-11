# Contributing to Campaign Forge

Thanks for your interest in improving Campaign Forge. This guide covers the
development setup, the standards the project holds itself to, and the workflow
for submitting changes.

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md). Security
problems are reported privately, as described in [SECURITY.md](SECURITY.md), not
in an issue.

## Ground rules

- **Free to run.** Nothing may require a paid service, a card or a paid model by
  default, and nothing may switch to one silently ([`docs/FREE_TIER_POLICY.md`](docs/FREE_TIER_POLICY.md)).
- **Honest numbers.** Benchmarks, metrics and claims in the docs come from real,
  dated runs ([`docs/BENCHMARKS.md`](docs/BENCHMARKS.md)). Simulated output is always
  labeled as simulated.
- **No secrets anywhere.** Not in code, tests, fixtures, issues or screenshots.

## Getting started

1. Fork and clone the repository.
2. Create and activate a virtual environment (Python 3.10+):

   ```bash
   python -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   ```

3. Install the package with development dependencies (add `ui` and `api` to run
   the web app, the HTTP API and their tests):

   ```bash
   pip install -e ".[dev,ui,api]"
   ```

4. (Optional but recommended) install the pre-commit hooks:

   ```bash
   pre-commit install
   ```

## Development workflow

Create a branch off `main`, make your change, and keep it focused. Before
opening a pull request, make sure every quality gate passes locally:

```bash
ruff check .            # lint
ruff format .           # format
mypy src                # strict type-check
pytest                  # tests, including the Streamlit AppTest suite (90% coverage floor)
python evals/run_eval.py --offline   # end-to-end eval smoke (no API key)
campaign-forge bench full-campaign --offline --dataset injection \
  --baseline src/campaign_forge/evaluation/baselines/full-campaign.injection.json
```

These are the same checks that run in continuous integration on Python 3.10,
3.11, 3.12, and 3.13, next to a Docker smoke test of the API and a `pip-audit`
of the dependencies. A pull request will not be merged unless all of them pass.

**Baselines.** CI compares three offline benchmarks with the baselines in
`src/campaign_forge/evaluation/baselines/`. If your change deliberately changes a
workflow's calls or tokens, rerun the benchmark with `--save-baseline` and say why
in the pull request.

**The web app.** `streamlit run app/streamlit_app.py` starts it in demo mode with no
key. Pages are tested headlessly with Streamlit's `AppTest` (`tests/test_app.py`).
CI runs Python 3.11+ with a newer Streamlit than 3.10 can install, so type-check UI
changes on 3.11 too if you can.

The `pytest` suite is fully mocked and proves the plumbing; the eval harness in
`evals/` measures real-world behaviour on a diverse, adversarial brief corpus
(see [`evals/README.md`](evals/README.md)). Run the offline smoke on every
change, and a live run (`python evals/run_eval.py`) when you touch prompts,
parsing, or the workflow.

## Coding standards

- **Type everything.** The package is checked with `mypy --strict`; add full
  annotations, including return types.
- **Validate at the boundary.** Prefer Pydantic models over raw dictionaries for
  data that crosses a module boundary.
- **No import-time side effects.** Importing a module must not read the network,
  raise on missing configuration, or configure global logging.
- **ASCII source.** Keep source files ASCII-only; if a non-ASCII character is
  genuinely needed at runtime, construct it in code (for example, `chr(0x2022)`).
- **Line length** is 100 characters (enforced by Ruff).
- **Docstrings** on public modules, classes, and functions; explain the "why",
  not just the "what".
- **Tests** accompany behavioural changes. The suite must stay fully offline -
  no real API keys or network calls. Inject a fake LLM client instead. When you
  fix a bug triggered by an unusual input, add that input shape to
  `src/campaign_forge/evaluation/datasets/corpus.json` so the eval harness guards against a regression.

## Adding a new agent

There are two paths. New agents should use the 2.0 contract; the workflow
engine runs them (see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), Part II).

**v1 pipeline agent** (what the CLI runs today):

1. Subclass `BaseAgent` in `src/campaign_forge/agents/`.
2. Set the `name` and `system_prompt` class attributes (the base class enforces
   this at class-creation time).
3. Implement `run(...)`, using `self._generate(...)` for model calls and the
   helpers in `parsing.py` for structured output. Wrap user-supplied or
   model-generated content with `self._data(content, label)` so it is framed
   as untrusted data.
4. Wire the agent into `CampaignWorkflow` and add tests using `FakeLLM`.

**2.0 agent** (registry-based):

1. Define a class with a `spec: ClassVar[AgentSpec]` and a
   `run(inputs, ctx) -> AgentOutput` method.
2. Make every model call through `ctx.complete(...)` or
   `ctx.complete_structured(...)`. These are budgeted, routed and recorded.
3. Register the class with `registry.register`, or ship it from another
   package through the `campaign_forge.agents` entry-point group.
4. Use it in a `WorkflowDefinition` (a built-in template, or a JSON file
   checked with `campaign-forge workflows validate`).
5. Meet the definition of done in [`docs/AGENT_CATALOG.md`](docs/AGENT_CATALOG.md),
   section 1, and test it with `FakeProvider` (see `tests/orchestration_helpers.py`).

The import layers in `tests/security/test_architecture.py` apply to all code: a
module may import only from its own layer or a lower one.

## Commit messages and pull requests

- Write clear, imperative commit messages ("Add retry-after backoff", not
  "added").
- Keep pull requests small and single-purpose. Open an issue first to discuss
  larger or breaking changes.
- Update the documentation (`README.md`, `docs/ARCHITECTURE.md`) and
  `CHANGELOG.md` when your change affects behaviour or public API.

## Reporting bugs and proposing features

Use the issue templates: **Bug report** asks for the version, the provider or
mode, a minimal reproduction, and the expected and actual behaviour. **Feature
request** asks for the problem first. Never include real API keys or other secrets
in an issue.
