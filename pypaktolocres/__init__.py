"""
pypaktolocres
=============

Stream a UE4/UE5 .pak, extract every FText English source string (without
unpacking the whole pak to disk), and build a .locres.

Depends on:
  * pyuepak  - pak reading (per-file streaming)
  * pylocres - locres read/write

Quick start
-----------
    from pypaktolocres import pak_to_locres

    pak_to_locres(
        pak="SurrounDead/Content/Paks/SurrounDead-Windows.pak",
        out="Game_en.locres",
        reference="fr_mod/Game_en.locres",   # optional: keep its structure/hashes
        prefixes=("SurrounDead/",),
    )

or as a command line tool:
    python -m pypaktolocres <pak> -o Game_en.locres [--reference fr.locres] [--csv out.csv]
"""
from __future__ import annotations

import os
import sys
from typing import Callable, Dict, Optional, Tuple

from pyuepak import PakFile
from pylocres import LocresVersion

from ._extract import extract_from_bytes, resolve, iter_pak_files
from ._build import build_locres, write_locres, PypaktolocresError

__version__ = "0.1.0"
__all__ = [
    "pak_to_locres",
    "extract_pak",
    "build_locres",
    "write_locres",
    "extract_from_bytes",
    "resolve",
    "PypaktolocresError",
]

ProgressFn = Optional[Callable[[int, int, int], None]]


def extract_pak(
    pak: str,
    prefixes: Tuple[str, ...] = ("SurrounDead/",),
    extensions: Tuple[str, ...] = (".uasset", ".uexp"),
    key_filter: Optional[set] = None,
    progress: ProgressFn = None,
    raw: bool = False,
) -> Dict:
    """
    Stream the pak and extract every FText found.

    :param pak: path to the .pak file
    :param prefixes: only read in-pak paths starting with these (default the game content)
    :param extensions: file suffixes to scan
    :param key_filter: optional set of keys to restrict extraction to (faster)
    :param progress: callback(done, total, keys_so_far)
    :param raw: if True, return the unresolved per-key source counts
        ``{key: {source: count}}`` instead of ``{key: source}``
    :return: ``{key: source}`` (most-common source wins) when ``raw=False``,
        else ``{key: {source: count}}`` when ``raw=True``
    """
    p = PakFile()
    p.read(pak)
    files = list(iter_pak_files(p, prefixes=prefixes, extensions=extensions))
    total = len(files)

    found = {}
    for idx, path in enumerate(files, 1):
        data = p.read_file(path)
        per = extract_from_bytes(data, key_filter=key_filter)
        for k, c in per.items():
            _merge(found, k, c)
        if progress and (idx % 500 == 0 or idx == total):
            progress(idx, total, len(found))

    return found if raw else resolve(found)


def _merge(found: dict, k: str, c: dict) -> None:
    tgt = found.get(k)
    if tgt is None:
        found[k] = dict(c)
    else:
        for s, n in c.items():
            tgt[s] = tgt.get(s, 0) + n


def pak_to_locres(
    pak: str,
    out: str,
    reference: Optional[str] = None,
    prefixes: Tuple[str, ...] = ("SurrounDead/",),
    extensions: Tuple[str, ...] = (".uasset", ".uexp"),
    key_filter: Optional[set] = None,
    namespace: str = "",
    version: LocresVersion = LocresVersion.Compact,
    overrides: Optional[Dict[str, str]] = None,
    keep_unresolved: bool = False,
    progress: ProgressFn = None,
) -> str:
    """
    One-shot: pak -> extract EN sources -> build .locres -> write to `out`.

    :return: the output path written
    """
    sources = extract_pak(pak, prefixes=prefixes, extensions=extensions,
                          key_filter=key_filter, progress=progress)
    locres = build_locres(sources, reference=reference, namespace=namespace,
                          version=version, overrides=overrides,
                          keep_unresolved=keep_unresolved)
    write_locres(locres, out)
    return out


def _human(n: int) -> str:
    for u in ("", "K", "M", "G"):
        if n < 1000:
            return f"{n:.0f}{u}"
        n /= 1000
    return f"{n:.0f}T"
