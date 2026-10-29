# Contributing

Thanks for helping. Bug reports, fixes, new checkers and evaluation results on other models are all welcome.

## Set up
```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt               # tested versions (constraints.txt), this folder editable, dev tools
pytest -q                                         # no API key needed
```
PostgreSQL tests run when `CASCADE_TEST_PG=postgresql://user:pass@host:5432/db` points at an empty test database.

## Before you open a pull request
- `ruff check .` and `mypy cascade_guard` pass.
- `pytest -q` passes; new behaviour comes with a test.
- No real API key is needed by any test: use `LLM_PROVIDER=fake`, `FakeLLMClient` or the stand-in server in the tests.
- Results you quote say how they were produced (seed, trials, model, simulation or real).
- Add a line to `CHANGELOG.md` under an "Unreleased" heading.

## Releasing (maintainers)
1. Set `__version__` in `cascade_guard/__init__.py`, move "Unreleased" in `CHANGELOG.md` to the new version, update
   `RELEASE_NOTES.md`.
2. Commit, then `git tag vX.Y.Z && git push --tags`.
3. `release.yml` checks that the tag matches the version, runs the tests, and publishes to PyPI, GitHub Releases and
   `ghcr.io/<owner>/cascade-guard`.
