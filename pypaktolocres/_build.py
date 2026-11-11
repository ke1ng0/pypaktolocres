"""
Build a .locres (EN source) from extracted {key: source} pairs using pylocres.

Two modes:
  * reference mode: load an existing .locres (e.g. the FR mod's), replace each
    entry's translation with the EN source, keep the original SourceStringHash,
    and append any new keys.  This keeps the file structurally identical to the
    proven-working reference.
  * fresh mode: build a brand-new locres from the extracted pairs.

The output uses LocresVersion.Compact (v1) to match the game / FR mod format.
"""
from __future__ import annotations

import os
from typing import Dict, Optional

from pylocres import LocresFile, LocresVersion, Namespace, Entry
from pylocres.crc_hash import str_crc32


class PypaktolocresError(Exception):
    pass


def _pick_namespace(locres: LocresFile, name: str) -> Namespace:
    ns = locres.namespaces.get(name)
    if ns is None:
        ns = Namespace(name)
        locres.add(ns)
    return ns


def build_locres(
    sources: Dict[str, str],
    reference: Optional[str] = None,
    namespace: str = "",
    version: LocresVersion = LocresVersion.Compact,
    overrides: Optional[Dict[str, str]] = None,
    keep_unresolved: bool = False,
) -> LocresFile:
    """
    Build a LocresFile.

    :param sources: {key(32-hex): EN source string}
    :param reference: path to an existing .locres to start from (optional)
    :param namespace: namespace for new / fresh entries (default "")
    :param version: output format (default Compact = v1, matches FR mod)
    :param overrides: {key: value} applied last (wins over sources)
    :param keep_unresolved: in reference mode, keep the reference value for keys
        with no extracted source (default False -> set them to "" for a clean template)
    :return: LocresFile (call .write(path) or .to_binary())
    """
    sources = dict(sources)
    if overrides:
        for k, v in overrides.items():
            sources[k] = v

    if reference:
        locres = LocresFile()
        locres.read(reference)
        # keep the reference's format (it is Compact for the FR mod)
        if locres.version < version:
            locres.version = version
        # fill / replace translations on existing entries
        for ns in locres:
            for e in ns:
                if e.key in sources:
                    e.translation = sources[e.key]
                elif not keep_unresolved:
                    e.translation = ""
        existing = {e.key for ns in locres for e in ns}
    else:
        locres = LocresFile()
        locres.version = version
        existing = set()

    ns = _pick_namespace(locres, namespace)
    added = 0
    for key, val in sources.items():
        if key in existing:
            continue
        # SourceStringHash = UE FString CRC32 of the source string
        ns.add(Entry(key, val, str_crc32(val), is_hash=True))
        added += 1

    locres._added_count = added  # convenience metadata
    return locres


def write_locres(locres: LocresFile, out_path: str) -> int:
    """Write a LocresFile to disk, creating parent dirs. Returns byte size."""
    d = os.path.dirname(os.path.abspath(out_path))
    if d:
        os.makedirs(d, exist_ok=True)
    locres.write(out_path)
    return os.path.getsize(out_path)
