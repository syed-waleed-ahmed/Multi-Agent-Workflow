## What and why

<!-- What does this change, and why is it needed? Link the issue it addresses. -->

## How it was tested

<!-- The commands you ran and what they showed. A live run, if you did one: provider, model, calls, tokens. -->

## Checklist

- [ ] `ruff check .`, `ruff format --check .` and `mypy src` pass
- [ ] `pytest` passes, and new behaviour has tests (offline: no keys, no network)
- [ ] `python evals/run_eval.py --offline` passes, and the Lab baselines still pass (or were refreshed on purpose, with the reason given)
- [ ] Source stays ASCII; no secrets anywhere, including in tests and fixtures
- [ ] Nothing requires a paid service, and no number in the docs is invented
- [ ] Docs and `CHANGELOG.md` are updated where behaviour changed
