# Filesystem and platform compatibility evidence

## Current evidence

| Environment | Evidence | Status and limits |
| --- | --- | --- |
| macOS / APFS (workspace) | Local Python 3.11–3.14 test suite; real ZIM open/search; SQLite path, transaction, close/reopen, backup/restore, and process-kill tests | Locally tested on the current macOS machine. Does not cover power loss. |
| macOS / APFS (`SteamDrive` volume) | 47 storage and sync-worker tests run with pytest temporary data on this mounted volume | `diskutil` reports the volume is on an internal device. This is a second APFS volume, not removable media, and does not validate drive disconnection. |
| Ubuntu / ext4 | Ubuntu CI job is configured locally | No hosted workflow or run is registered remotely; no direct ext4 mount test was run locally. |
| Windows / NTFS | Windows CI job is configured locally | Hosted job has not run; no Windows machine was available locally. |
| macOS / exFAT | Earlier `PLAN.md` notes report a DELETE-journal test on an exFAT disk image | Historical project note only; not independently reproduced in this milestone. No exFAT volume is currently mounted. |

The checked-out machine is macOS 27.2 ARM64. `df`/`diskutil` show the workspace on APFS and a separate mounted `SteamDrive` APFS volume; `diskutil` reports the latter's device location as internal. The available free space on the workspace volume was about 211 GiB at inspection. No ext4, NTFS, or exFAT volume is mounted. CI configuration is not a CI result.

The second-volume command ran `tests/test_overlay.py`, `tests/test_backup_overlay.py`, and `tests/test_sync_worker.py` with pytest's `--basetemp` set beneath a fresh directory on `/Volumes/SteamDrive`; 48 tests passed. This validates ordinary database and backup operations on that mounted APFS volume only.

## Storage behavior and guarantees

- The default SQLite journal mode is `DELETE`. This avoids depending on SQLite WAL shared-memory behavior on exFAT; it does not make exFAT journaled or protect against all device disconnects, write reordering, or power loss.
- The overlay's article row, FTS trigger updates, and backlog removal share one SQLite transaction. Listing changes, deletion updates, backlog changes, and cursor advancement also share one transaction.
- A process kill at tested transaction boundaries on APFS recovers to either the committed old state or the committed new state after reopening SQLite. This is process-crash evidence, not proof for sudden power loss or every storage device.
- WekI does not automatically reconnect its open SQLite connection after a drive is removed. Reinsert the drive and restart WekI; inspect the database before continuing if disconnection occurred during a write.
- Physical device removal while idle, fetching, writing, or immediately after a commit was not tested: no removable device was available. Process termination tests are not substitutes for unplugging storage.
- The downloader writes a `.part` file next to the destination and renames it after checksum verification. This relies on same-volume rename behavior; the project does not claim atomic rename durability on exFAT or across device removal.
- `WAL` is configurable but is not the recommended portable-drive mode and has not been validated on exFAT. Prefer `DELETE` for the portable-drive use case.
- `scripts/backup_overlay.py` uses SQLite's online backup API, verifies the result, and refuses to overwrite an existing path. The unit test copies that backup to a new location, reopens it, and checks article/FTS data, cursor, backlog, and integrity. The script backs up only SQLite; preserve the matching ZIM and configuration separately. An interrupted backup destination is not trusted until integrity-checked.
- Paths use `pathlib` and resolve relative to the application root. Tests cover Unicode and spaces in a SQLite path; they do not prove every Windows path-length, removable-media, or case-sensitivity behavior.

## Reproducible manual filesystem procedure

Use a disposable copy of the archive and database on each target filesystem. Never run a destructive test against the only copy of a library.

1. Record OS, filesystem, mount options, Python, SQLite, libzim, archive SHA-256, free space, and device type.
2. Copy the verified ZIM to the target volume, make the archive read-only using that OS's file attributes, and run the real-ZIM benchmark from the README/report.
3. Create a new database path on the same volume and run `uv run --locked python scripts/benchmark_overlay.py --articles 100000 --database <new-path>`. Confirm `sqlite_integrity: ok`, record the output, close the process, and rerun against a fresh database.
4. Run the tests on that OS. To place test databases on the target volume, configure pytest's `--basetemp` to a new disposable directory there.
5. On a disposable device only, repeat with `journal_mode = "DELETE"` while repeatedly opening/searching and writing the overlay. Eject cleanly between runs. Any physical disconnect/power interruption is a manual destructive test and must use recoverable copies.
6. Reopen SQLite, run `PRAGMA integrity_check`, check the cursor/backlog, and compare article counts/revisions. Record failures and do not infer safety from a successful clean shutdown.

## Compatibility conclusions

Only macOS/APFS behavior has been locally exercised for this milestone. Ubuntu/ext4, Windows/NTFS, and exFAT remain unverified until their jobs or manual procedures actually run. The active GitHub CLI identity is `lethebyte`, which cannot access the private repository; an already-authorized owner account can read it. The owner-account API reports zero registered workflows and zero runs because local workflow changes have not been pushed. No remote configuration or credentials were changed. SQLite rollback journaling provides transactional recovery under its documented filesystem assumptions; it cannot turn a non-journaling removable filesystem into a durable storage system.
