# Real ZIM benchmarks

I tested two real archives to separate two different questions: does the reader handle the ZIM format at all, and does it hold up on a materially larger dataset. The small OpenZIM wiki answers the first on a compact archive. The 478.5 MB Afrikaans Wikipedia snapshot answers the second with a bigger archive and index. Neither one is full English Wikipedia, and I'm not pretending otherwise.

## Dataset and provenance

- Archive: `openzim_en_all_nopic_2026-11.zim`
- Source: official Kiwix download catalog, `https://download.kiwix.org/zim/other/`
- ZIM source metadata: `wiki.openzim.org`
- Source page license notice: CC BY-SA 3.0 unless otherwise noted
- Size: 3,272,988 bytes
- SHA-256: `c54f875491f380a1e1427c138cfb1b2cea7674aa9adc893b46bbbc7460f7d464`
- Contents: 97 articles, 176 entries, new namespace scheme, full-text index
- Metadata: title `OpenZim Wiki`, date `2026-11-05`, language `eng`

I fetched the archive for this test only and it's not in the repository. It's a genuine, compact Kiwix ZIM, but it's the OpenZIM wiki, not a Wikipedia snapshot, so it says nothing about how a full Wikipedia archive would perform.

## Reproduction

Check your free disk space before downloading. The tested archive is about 3.27 MB.

```bash
curl -fL \
  -H 'User-Agent: WekI/0.1 (https://github.com/aymane-amounas/WekI; bot)' \
  'https://download.kiwix.org/zim/other/openzim_en_all_nopic_2026-11.zim' \
  -o /tmp/openzim_en_all_nopic_2026-11.zim
printf '%s  %s\n' \
  'c54f875491f380a1e1427c138cfb1b2cea7674aa9adc893b46bbbc7460f7d464' \
  '/tmp/openzim_en_all_nopic_2026-11.zim' | shasum -a 256 -c
uv run --locked python scripts/benchmark_overlay.py \
  --zim /tmp/openzim_en_all_nopic_2026-11.zim \
  --zim-article-path How-to_create_a_Python_scraper \
  --skip-overlay
```

## Results

Measured 2026-11-10 on macOS 27.2, ARM64, Python 3.12.13, SQLite 3.53.1.

| Metric | Result |
| --- | ---: |
| Archive open | 0.0026 s |
| Metadata reads (`Title`, `Date`, `Language`) | < 0.0001 s |
| Article lookup (33,551-byte article), p50 | 0.002 ms |
| Article lookup, p95 | 0.013 ms |
| Full-text search for `Wikimedia`, p50 | 1.400 ms |
| Full-text search, p95 | 1.636 ms |
| Search results returned | 10 |
| Missing path returns `None` | yes |
| Peak process RSS | 40.4 MiB |

A separate integration smoke check confirmed the main-page redirect, article retrieval, metadata, full-text results, missing-page behavior, and overlay precedence over a ZIM article. The `ZimReader` resolved the archive's redirecting main entry to `OpenZIM`, and overlay content at that path won in `Resolver`.

These are warm local measurements on a tiny archive. They don't predict cold-cache timings, memory for large archives, large-Wikipedia article sizes, sync time, filesystem removal behavior, or how another machine behaves. A separate generated test ZIM verifies retrieval of a 2 MiB article; the largest HTML article this report scanned from the real archive was 33,551 bytes.

## Larger real Wikipedia archive

### Dataset and provenance

- Archive: `wikipedia_af_all_nopic_2026-10.zim`
- Source: official Kiwix catalog, `https://download.kiwix.org/zim/wikipedia/`
- Archive size: 478,509,329 bytes (about 456 MiB)
- SHA-256: `a9ac061f6c74c1db9dcd146d50e390ed4b757de6c711322bd7856cb59988bc9f`
- Metadata: title `Wikipedia`, date `2026-10-06`, language `afr`
- Articles: 163,636
- Full-text index: present

Before downloading I checked the exact content length and the `.sha256` sidecar; the workspace had about 211 GiB free. The archive was pulled from a Kiwix-listed mirror, checksum-verified, benchmarked, and deleted from the temporary directory after the run. No project data changed.

### Results

Measured 2026-11-10 on macOS 27.2, ARM64, Python 3.12.13, SQLite 3.53.1. The benchmark does warm repeated reads and searches; it does not build the ZIM's precomputed index.

| Metric | Result |
| --- | ---: |
| Archive open | 0.0150 s |
| Metadata reads | 0.0003 s |
| Article lookup (`Tuisblad`, 77,950 bytes), p50 / p95 | 0.004 / 0.011 ms |
| Redirect resolution (`!` → `Uitroepteken`), p50 / p95 | 0.002 / 0.019 ms |
| Full-text search for `Wikimedia`, p50 / p95 | 4.217 / 6.020 ms |
| Search results returned | 10 |
| Title suggestions for `Wik`, p50 / p95 | 0.684 / 1.990 ms |
| Suggestions returned | 10 |
| Missing path returns `None` | yes |
| Repeated archive open/release cycles | 5 |
| Open/release cycle p50 | 7.826 ms |
| File descriptors before / after cycles | 9 / 9 |
| Process peak RSS | 71.4 MiB |

libzim printed non-fatal `No stemming for language 'af'` warnings during the search loop; searches and suggestions still returned results. `ZimReader` doesn't expose a native `close()`, so the benchmark releases each reader by dropping its Python reference, and the file-descriptor count came back unchanged on this macOS run. That's one data point, not proof of resource behavior on other bindings or operating systems.

This archive is much bigger than the 3.27 MB smoke-test one, but it's still only 163,636 articles and it isn't English Wikipedia. Kiwix built its indexes, not WekI. So these warm-cache results don't establish full-Wikipedia open time, memory, search performance, indexing time, or portable-filesystem behavior.

### Reproduction

Check disk space before downloading. At the measured date, the official catalog's latest matching archive was 478,509,329 bytes.

```bash
uv run --locked python scripts/download_zim.py --lang af --dir /tmp/weki-af
uv run --locked python scripts/benchmark_overlay.py \
  --zim /tmp/weki-af/wikipedia_af_all_nopic_2026-10.zim \
  --skip-overlay
```

The downloader verifies the official SHA-256 sidecar. Catalog contents change over time, so compare the downloaded filename and hash against the values above before you treat a later run as the same dataset.
