# Contributing to Campaign Forge

Thanks for your interest in improving Campaign Forge. This guide covers the
development setup, the standards the project holds itself to, and the workflow
for submitting changes.

## Getting started

1. Fork and clone the repository.
2. Create and activate a virtual environment (Python 3.10+):

   ```bash
   python -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   ```

3. Install the package with development dependencies:

   ```bash
   pip install -e ".[dev]"
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
pytest                  # tests
```

These are the same checks that run in continuous integration on Python 3.10,
3.11, and 3.12. A pull request will not be merged unless all of them pass.

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
  no real API keys or network calls. Inject a fake LLM client instead.

## Adding a new agent

1. Subclass `BaseAgent` in `src/campaign_forge/agents/`.
2. Set the `name` and `system_prompt` class attributes (the base class enforces
   this at class-creation time).
3. Implement `run(...)`, using `self._generate(...)` for model calls and the
   helpers in `parsing.py` for structured output.
4. Wire the agent into `CampaignWorkflow` and add tests using `FakeLLM`.

## Commit messages and pull requests

- Write clear, imperative commit messages ("Add retry-after backoff", not
  "added").
- Keep pull requests small and single-purpose. Open an issue first to discuss
  larger or breaking changes.
- Update the documentation (`README.md`, `docs/ARCHITECTURE.md`) and
  `CHANGELOG.md` when your change affects behaviour or public API.

## Reporting bugs

Open an issue with a minimal reproduction, the expected behaviour, the actual
behaviour, and your environment (OS and Python version). Never include real API
keys or other secrets in an issue.
