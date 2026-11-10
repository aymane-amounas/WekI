#!/usr/bin/env python3
"""idk how that did work but it did work, so here we are.
Download the latest Kiwix ZIM into data/, resumable, verified against its .sha256 sidecar."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path

import httpx

INDEX_URL = "https://download.kiwix.org/zim/wikipedia/"
USER_AGENT = "WekI/0.1.0-alpha.1 (https://github.com/aymane-amounas/WekI; bot)"
CHUNK = 1024 * 1024
PROBE_BYTES = 2 * 1024 * 1024
PROBE_SECONDS = 4
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class ChecksumError(Exception):
    pass


def pick_latest(index_html: str, lang: str, flavour: str) -> str:
    pattern = rf"wikipedia_{re.escape(lang)}_all_{re.escape(flavour)}_\d{{4}}-\d{{2}}\.zim"
    names = set(re.findall(pattern, index_html))
    if not names:
        raise SystemExit(f"no ZIM found for lang={lang} flavour={flavour}")
    return max(names)


def fetch_sha256(client: httpx.Client, url: str) -> str:
    resp = client.get(f"{url}.sha256")
    resp.raise_for_status()
    return resp.text.split()[0].lower()


def parse_mirrors(meta4: str) -> list[str]:
    urls = re.findall(r"<url[^>]*>\s*(https?://[^<\s]+)", meta4)
    return [url for url in urls if url.endswith(".zim")]


def probe(client: httpx.Client, url: str) -> float:
    """Bytes per second over a short ranged read; 0 when the mirror fails."""
    received = 0
    start = time.monotonic()
    headers = {"Range": f"bytes=0-{PROBE_BYTES - 1}"}
    try:
        with client.stream("GET", url, headers=headers, timeout=PROBE_SECONDS) as resp:
            if resp.status_code != 206:
                return 0.0
            for chunk in resp.iter_bytes(64 * 1024):
                received += len(chunk)
                if time.monotonic() - start > PROBE_SECONDS:
                    break
    except httpx.HTTPError:
        return 0.0
    return received / max(time.monotonic() - start, 0.001)


def fastest_mirror(client: httpx.Client, url: str) -> str:
    """The default redirect can land on a slow mirror, so measure them from the .meta4 list."""
    resp = client.get(f"{url}.meta4")
    if resp.status_code != 200:
        return url
    speeds = {mirror: probe(client, mirror) for mirror in parse_mirrors(resp.text)}
    best = max(speeds, key=speeds.__getitem__, default=url)
    return best if speeds.get(best) else url


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def download(client: httpx.Client, url: str, dest: Path, expected_sha256: str) -> Path:
    """Fetch url to dest via dest.part, resuming a previous partial download."""
    part = dest.with_name(dest.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}

    with client.stream("GET", url, headers=headers) as resp:
        if resp.status_code != 416:
            resp.raise_for_status()
            mode = "ab" if resp.status_code == 206 else "wb"
            done = offset if mode == "ab" else 0
            total = done + int(resp.headers.get("content-length", 0))
            with part.open(mode) as f:
                for chunk in resp.iter_bytes(CHUNK):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        print(f"\r{done / 1e9:.2f}/{total / 1e9:.2f} GB", end="", flush=True)
            print()

    actual = sha256_file(part)
    if actual != expected_sha256:
        part.unlink()
        raise ChecksumError(f"sha256 mismatch: expected {expected_sha256}, got {actual}")
    part.rename(dest)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", default="en-simple", help="en-simple or en")
    parser.add_argument("--flavour", default="nopic", help="nopic, mini or maxi")
    parser.add_argument("--dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    timeout = httpx.Timeout(30, read=120)
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        index = client.get(INDEX_URL)
        index.raise_for_status()
        name = pick_latest(index.text, args.lang, args.flavour)
        dest = args.dir / name
        if dest.exists():
            print(f"already downloaded: {dest}")
            return 0
        args.dir.mkdir(parents=True, exist_ok=True)
        url = INDEX_URL + name
        expected = fetch_sha256(client, url)
        url = fastest_mirror(client, url)
        print(f"downloading {url}")
        try:
            download(client, url, dest, expected)
        except ChecksumError as exc:
            print(exc, file=sys.stderr)
            return 1
    print(f"ok: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
