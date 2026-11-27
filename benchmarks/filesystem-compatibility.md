# Filesystem and platform compatibility evidence

## Current evidence

| Environment | Evidence | Status and limits |
| --- | --- | --- |
| macOS / APFS (workspace) | Local Python 3.11–3.14 test suite; real ZIM open/search; SQLite path, transaction, close/reopen, backup/restore, and process-kill tests | Locally tested on the current macOS machine. Does not cover power loss. |
| macOS / APFS (`SteamDrive` volume) | 47 storage and sync-worker tests run with pytest temporary data on this mounted volume | `diskutil` reports the volume is on an internal device. This is a second APFS volume, not removable media, and does not validate drive disconnection. |
| Ubuntu / ext4 | Ubuntu CI job is configured locally | No hosted workflow or run is registered remotely; no direct ext4 mount test was run locally. |
| Windows / NTFS | Windows CI job is configured locally | Hosted job has not run; no Windows machine was available locally. |
| macOS / exFAT | Earlier `PLAN.md` notes report a DELETE-journal test on an exFAT disk image | Historical project note only; not independently reproduced in this milestone. No exFAT volume is currently mounted. |

So here's the honest state of it. The checked-out machine is macOS 27.2 ARM64. `df` and `diskutil` put the workspace on APFS and show a separate mounted `SteamDrive` APFS volume, which `diskutil` reports as internal rather than removable. Free space on the workspace volume was about 211 GiB when I checked. No ext4, NTFS, or exFAT volume is mounted. And a configured CI job is not a CI result, which is why the table keeps those two apart.

The second-volume run pointed pytest's `--basetemp` at a fresh directory on `/Volumes/SteamDrive` and ran `tests/test_overlay.py`, `tests/test_backup_overlay.py`, and `tests/test_sync_worker.py`; 48 tests passed. That validates ordinary database and backup operations on that mounted APFS volume, and nothing more.

## Storage behavior and guarantees

What I'm willing to claim, and the limits on each claim:

- The default SQLite journal mode is `DELETE`. This avoids relying on SQLite's WAL shared-memory behavior on exFAT. It does not make exFAT journaled and does not protect against every device disconnect, write reordering, or power loss.
- The overlay's article row, its FTS trigger updates, and the backlog removal share one SQLite transaction. Listing changes, deletion updates, backlog changes, and cursor advancement also share one transaction.
- A process kill at the tested transaction boundaries on APFS recovers to either the committed old state or the committed new state after SQLite reopens. That's process-crash evidence, not proof for sudden power loss or for every storage device.
- WekI does not auto-reconnect its open SQLite connection after a drive is pulled. Reinsert the drive and restart WekI, and if the disconnect happened mid-write, inspect the database before carrying on.
- Physical device removal while idle, fetching, writing, or right after a commit was not tested, because no removable device was available. Killing the process is not a substitute for unplugging storage.
- The downloader writes a `.part` file next to the destination and renames it after checksum verification. That leans on same-volume rename behavior; the project does not claim atomic rename durability on exFAT or across device removal.
- `WAL` is configurable but it is not the recommended portable-drive mode and has not been validated on exFAT. For a portable drive, prefer `DELETE`.
- `scripts/backup_overlay.py` uses SQLite's online backup API, verifies the result, and refuses to overwrite an existing path. The unit test copies that backup to a new location, reopens it, and checks article and FTS data, cursor, backlog, and integrity. The script backs up SQLite only; keep the matching ZIM and configuration separately. An interrupted backup destination isn't trusted until it's integrity-checked.
- Paths use `pathlib` and resolve relative to the application root. Tests cover Unicode and spaces in a SQLite path; they don't prove every Windows path-length, removable-media, or case-sensitivity behavior.

## Reproducible manual filesystem procedure

Use a disposable copy of the archive and database on each target filesystem. Never run a destructive test against the only copy of a library.

1. Record OS, filesystem, mount options, Python, SQLite, libzim, archive SHA-256, free space, and device type.
2. Copy the verified ZIM to the target volume, make the archive read-only using that OS's file attributes, and run the real-ZIM benchmark from the README/report.
3. Create a new database path on the same volume and run `uv run --locked python scripts/benchmark_overlay.py --articles 100000 --database <new-path>`. Confirm `sqlite_integrity: ok`, record the output, close the process, and rerun against a fresh database.
4. Run the tests on that OS. To put the test databases on the target volume, point pytest's `--basetemp` at a new disposable directory there.
5. On a disposable device only, repeat with `journal_mode = "DELETE"` while repeatedly opening/searching and writing the overlay. Eject cleanly between runs. Any physical disconnect or power interruption is a manual destructive test and must use recoverable copies.
6. Reopen SQLite, run `PRAGMA integrity_check`, check the cursor and backlog, and compare article counts and revisions. Record failures, and don't infer safety from a clean shutdown.

## Compatibility conclusions

Only macOS/APFS has actually been exercised locally for this milestone. Ubuntu/ext4, Windows/NTFS, and exFAT stay unverified until their jobs or the manual procedure above actually run. The active GitHub CLI identity is `lethebyte`, which can't access the private repository; an already-authorized owner account can read it. The owner-account API reports zero registered workflows and zero runs because the local workflow changes haven't been pushed. No remote configuration or credentials were changed. SQLite rollback journaling gives you transactional recovery under its documented filesystem assumptions, but it can't turn a non-journaling removable filesystem into durable storage.
