# Contributing

Bug reports, ideas, and pull requests are all welcome.

For anything bigger than a small fix, open an issue first so we can agree on the approach before you write code. Keep changes focused on one thing, and please don't describe behavior as implemented when it's still planned. One more ask that's specific to this project: be gentle with Wikimedia's servers. Keep the configured User-Agent, the request delay, and the concurrency limits as they are.

## Development setup

```bash
git clone https://github.com/aymane-amounas/WekI.git
cd WekI
uv sync --locked
uv run --locked pytest -q
```

The tests run on local fixtures and mocked network responses, so you don't need an archive or a connection for them. The live tests and benchmarks are documented under [`benchmarks/`](benchmarks/). If your change touches performance or sync, include before/after numbers.

## Pull requests

Keep it to one logical change per PR and say what it does and why. Add or update tests when behavior changes, and update the README if the change is user-visible.

## License

By contributing, you agree your contribution is licensed under the project's license, [GPL-3.0-or-later](LICENSE).

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
