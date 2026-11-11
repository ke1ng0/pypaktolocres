"""
Command line interface.

  python -m pypaktolocres <pak-or-directory> -o Game_en.locres
                          [--reference fr.locres] [--csv out.csv] [--ns ""]

Reads needed files from a .pak or an unpacked asset directory, extracts FText
and StringTable sources, and writes a .locres (Compact v1 by default).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time

from pylocres import LocresVersion
from . import extract_pak, build_locres, write_locres


def _pct(done, total):
    return f"\r  {done}/{total} files ({done*100//max(total,1)}%)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="pypaktolocres",
        description="Extract FText and StringTable sources from UE assets and build a .locres.",
    )
    ap.add_argument("pak", help="path to a .pak file or unpacked asset directory")
    ap.add_argument("-o", "--out", required=True, help="output .locres path")
    ap.add_argument("--reference", default=None,
                    help="optional existing .locres to reuse structure/hashes from")
    ap.add_argument("--csv", default=None,
                    help="also write a reference CSV (key,namespace,en_text,th_text)")
    ap.add_argument("--prefix", action="append", default=None,
                    help="only read in-pak paths starting with this (repeatable; default SurrounDead/)")
    ap.add_argument("--ext", action="append", default=None,
                    help="file extension to scan (repeatable; default .uasset .uexp)")
    ap.add_argument("--ns", default="", help="namespace for new/fresh entries (default empty)")
    ap.add_argument("--keep-unresolved", action="store_true",
                    help="in reference mode, keep the reference value for keys with no extracted source (default: set them empty)")
    ap.add_argument("--version", default="compact",
                    choices=["legacy", "compact", "optimized", "cityhash"],
                    help="output locres format (default compact = v1)")
    ap.add_argument("-q", "--quiet", action="store_true", help="less progress output")
    args = ap.parse_args(argv)

    prefixes = tuple(args.prefix) if args.prefix else (
        () if os.path.isdir(args.pak) else ("SurrounDead/",)
    )
    extensions = tuple(args.ext) if args.ext else (".uasset", ".uexp")
    version = {
        "legacy": LocresVersion.Legacy,
        "compact": LocresVersion.Compact,
        "optimized": LocresVersion.Optimized,
        "cityhash": LocresVersion.CityHash,
    }[args.version]

    def progress(done, total, keys):
        if args.quiet:
            return
        sys.stderr.write(_pct(done, total) + f"  keys={keys}")
        if done == total:
            sys.stderr.write("\n")

    t0 = time.time()
    print(f"Reading source: {args.pak}")
    print(f"  prefixes={prefixes} ext={extensions}")
    sources = extract_pak(args.pak, prefixes=prefixes, extensions=extensions,
                          progress=(None if args.quiet else progress))
    if not args.quiet:
        print(f"  extracted {len(sources)} unique keys in {time.time()-t0:.1f}s")

    locres = build_locres(sources, reference=args.reference, namespace=args.ns,
                          version=version, keep_unresolved=args.keep_unresolved)
    n_added = getattr(locres, "_added_count", 0)
    total_entries = sum(len(ns) for ns in locres)
    write_locres(locres, args.out)
    print(f"Wrote {args.out} ({total_entries} entries; {n_added} new keys added)")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, quoting=csv.QUOTE_ALL, escapechar="\\")
            w.writerow(["key", "namespace", "en_text", "th_text"])
            for ns in locres:
                for e in ns:
                    w.writerow([e.key, ns.name, e.translation, ""])
        print(f"Wrote {args.csv}")

    print(f"Done in {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
