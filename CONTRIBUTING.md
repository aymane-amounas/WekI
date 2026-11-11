# Contributing

Bug reports, ideas, and pull requests are welcome.

## Before you start

- For anything larger than a small fix, open an issue first so we can agree on the approach.
- Keep changes focused. Do not describe planned behavior as implemented.
- Be respectful to Wikimedia's servers: keep the configured User-Agent, request delay, and concurrency limits.

## Development setup

```bash
git clone https://github.com/aymane-amounas/WekI.git
cd WekI
uv sync --locked
uv run --locked pytest -q
```

Tests use local fixtures and mocked network responses. Live tests and benchmarks are documented in [`benchmarks/`](benchmarks/). If your change affects performance or synchronization, include before/after numbers.

## Pull requests

- One logical change per PR, with a clear description of what and why.
- Add or update tests for behavior changes.
- Update the README when user-visible behavior changes.

## License

By contributing, you agree that your contribution is licensed under the project's license, [GPL-3.0-or-later](LICENSE).

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
