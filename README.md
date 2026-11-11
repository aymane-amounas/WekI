<p align="center">
  <img src="assets/wiki-logo.svg" alt="WekI logo" width="530">
</p>

<p align="center">
  <strong>Wikipedia that travels with you.</strong>
  <br>
  Read offline. Catch up online.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.1.0--alpha.1-blue" alt="Version 0.1.0-alpha.1">
  <img src="https://img.shields.io/badge/status-experimental-orange" alt="Experimental">
  <img src="https://img.shields.io/badge/license-GPL--3.0--or--later-green" alt="GPL-3.0-or-later">
</p>

WekI is an offline Wikipedia reader that keeps your local knowledge library up to date.

It combines a Kiwix ZIM archive with a lightweight SQLite database. Read your downloaded Wikipedia snapshot anywhere, then synchronize newer articles and changes when you're back online.

## At a glance

| Name | What it does | Stack | Demo |
|---|---|---|---|
| **WekI** | Offline Wikipedia reader that syncs newer articles when you're back online | Python · SQLite · libzim | [Screenshots](#screenshots) · [Live-sync report](benchmarks/live-sync-2026-11-10.md) · [Quickstart](#get-started) |

> No hosted link: WekI is a local-first, single-user app. The reproducible local demo is the [Get started](#get-started) quickstart; see [Screenshots](#screenshots) and the [live-sync report](benchmarks/live-sync-2026-11-10.md) for recorded evidence.

## Screenshots

<table>
  <tr>
    <td><img src="assets/screenshots/home.png" alt="WekI home page with 400,061 local articles" width="420"></td>
    <td><img src="assets/screenshots/article.png" alt="Article updated after the snapshot date" width="420"></td>
  </tr>
  <tr>
    <td align="center"><sub>Home: 400,061 Simple English articles on disk</sub></td>
    <td align="center"><sub>An article synced newer than the ZIM snapshot</sub></td>
  </tr>
  <tr>
    <td colspan="2" align="center"><img src="assets/screenshots/status.png" alt="Sync status page" width="560"></td>
  </tr>
  <tr>
    <td colspan="2" align="center"><sub>Sync status: updated, removed, and queued articles with cursor and last result</sub></td>
  </tr>
</table>

## Why WekI?

- **Read anywhere.** Access your local Wikipedia without an internet connection.
- **Stay updated.** Synchronize newer articles when you're online.
- **Keep your data.** Updates are stored separately from your original archive.
- **Pick up where you left off.** Unfinished synchronization work stays queued.
- **Search locally.** Find content across your archive and newer articles.

<sub>WekI is an experimental single-user application. Simple English Wikipedia is the practical synchronization target. Full English Wikipedia synchronization is not yet supported at scale.</sub>

## Get started

### Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Disk space for your selected Wikipedia archive and local database.

### Install

```bash
git clone https://github.com/aymane-amounas/WekI.git
cd WekI

uv sync --locked
uv run --locked python scripts/download_zim.py
uv run --locked python run.py
```

Open **http://127.0.0.1:8787** in your browser.

Your local archive works without synchronization. When you have an internet connection, select **Sync → Sync now** to fetch newer content.

If WekI was started before the ZIM download finished, restart it so it discovers the archive. To use a different port, run `uv run --locked python run.py --port <port>`.

## How it works

WekI has two sources of knowledge:

```text
       Wikipedia ZIM
       (Read-only)
            |
            v
      Article Reader
            ^
            |
       SQLite Overlay
       (Newer changes)
            ^
            |
      Wikimedia API
       (When online)
```

The ZIM archive is the original snapshot. SQLite stores newer articles, redirects, deletions, search data, and synchronization progress.

When an article is requested, WekI checks the updated content first, then deletion records and redirects, and finally the original archive.

During synchronization, discovered changes enter a persistent backlog. WekI fetches them gradually and keeps unfinished work for later runs.

## Measured results

Measured on macOS (Apple Silicon). Full method, reproduction steps, and limits are in [`benchmarks/`](benchmarks/).

| What | Result | Report |
|---|---:|---|
| Real ZIM article lookup, p50 / p95 | 0.002 / 0.013 ms | [real-zim-openzim](benchmarks/real-zim-openzim.md) |
| Real ZIM full-text search, p50 / p95 | 1.40 / 1.64 ms | [real-zim-openzim](benchmarks/real-zim-openzim.md) |
| Overlay upsert with FTS, 100k articles | 68,151 articles/s | [overlay-macos-arm64](benchmarks/overlay-macos-arm64.md) |
| Updated-article search, p50 / p95 | 0.42 / 0.48 ms | [overlay-macos-arm64](benchmarks/overlay-macos-arm64.md) |
| Peak memory (overlay benchmark) | 37.7 MiB | [overlay-macos-arm64](benchmarks/overlay-macos-arm64.md) |
| Live sync against simple.wikipedia.org | initial + incremental runs, 0 failed fetches, integrity `ok` | [live-sync-2026-11-10](benchmarks/live-sync-2026-11-10.md) |

The live sync test is a smoke test over short windows, not a throughput benchmark. The test suite (188 tests) uses local fixtures and mocked network responses.

## For developers

Built with Python, SQLite, and [libzim](https://github.com/kiwix/libzim).

- **Reader:** resolves articles across the ZIM and SQLite overlay.
- **Storage:** keeps the original ZIM read-only and stores updates in SQLite.
- **Search:** combines archive search with overlay search.
- **Sync worker:** consumes Wikimedia revision and page-log events, fetches article content, and maintains a persistent cursor and backlog.
- **Recovery:** uses database transactions to keep article updates, backlog changes, and cursor progress consistent.
- **Networking:** uses a bot User-Agent, bounded concurrency, `maxlag`, retry handling, and backoff.

### for your development setup

```bash
uv sync --locked --all-groups

uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Tests use local fixtures and mocked network responses. Real archive and synchronization tests are documented separately in `benchmarks/`.

## Data and backups

Your ZIM archive is never modified by WekI.

Back up your SQLite overlay:

```bash
uv run --locked python scripts/backup_overlay.py backups/overlay.db
```

Keep the matching ZIM archive and `config.toml` separately. A database backup alone is not a complete backup of your library.

For restore instructions and storage limitations, see [the recovery documentation](benchmarks/filesystem-compatibility.md) and project documentation.

## Current limitations

WekI is still evolving.

- Full English Wikipedia synchronization at scale is not solved.
- Removable-drive reliability and exFAT, NTFS, and ext4 compatibility are not certified.
- Hosted CI has not yet been verified.
- AI search, image synchronization, packaging, and multilingual support are not implemented.

See the [benchmark reports](benchmarks/) for actual test results and compatibility evidence.

## Contributing

Found a bug or have an idea?

- [Open an issue](https://github.com/aymane-amounas/WekI/issues)
- [Explore the source](https://github.com/aymane-amounas/WekI)

Contributions and thoughtful feedback are welcome.

## License

WekI's code is licensed under **GPL-3.0-or-later**.

Wikipedia content is subject to its own licensing, including **CC BY-SA 4.0** attribution and share-alike requirements.

---

<p align="center">
  Built with curiosity. Engineered with purpose.
</p>

---

<sub>Built by [Aymane](https://github.com/aymane-amounas)</sub>
