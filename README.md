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

If WekI was started before the ZIM download finished, restart it after the download so it discovers the archive. In the current local setup, **http://127.0.0.1:8787** is the earlier instance showing the download page, while the ZIM-backed main page is at **http://127.0.0.1:8788**. To start another instance on that alternate port, run `uv run --locked python run.py --port 8788`.

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

---

side note: IDK HOW THIS WORKED BUT IT DID
