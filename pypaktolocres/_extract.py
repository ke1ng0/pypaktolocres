"""
pypaktolocres - extract FText EN sources from a UE .pak and build a .locres.

Streaming design: files are read one-at-a-time from the pak (pyuepak) and
never unpacked to disk in bulk.

Extraction uses two byte-level patterns found in cooked UE assets:

  Pattern A  (Kismet bytecode FText, EX_TextConst / LocalizedText):
      ... [srcTok][SRC][00] [keyTok][KEY32][00] [nsTok][NS][00] ...
      where srcTok/keyTok in {0x1F EX_StringConst, 0x34 EX_UnicodeStringConst}

  Pattern B  (FText / key->source map inside export data):
      [i32 33][KEY32][00] [i32 srcLen][SRC][00]
      (the 32-hex FText Key stored as a 33-byte ASCII FString, immediately
       followed by its SourceString as another FString)

A 32-hex "key" is only accepted when it sits in one of these valid FText
contexts, which keeps the false-positive rate very low.
"""
from __future__ import annotations

import re
import struct
from collections import Counter, defaultdict
from typing import Callable, Iterable, Iterator, Optional

# A 32-hex run (case-insensitive). We normalize to upper-case (UE keys are upper).
_HEX32 = re.compile(rb"[0-9A-Fa-f]{32}")
_HEXBYTES = b"0123456789abcdefABCDEF"

TOK_ASCII = 0x1F    # EX_StringConst
TOK_UNICODE = 0x34  # EX_UnicodeStringConst


def _try_ascii(data: bytes, tokpos: int, end_nul: int) -> Optional[str]:
    """ASCII FText source: token at tokpos, bytes tokpos+1..end_nul-1, NUL at end_nul."""
    if tokpos < 0 or end_nul - tokpos - 1 < 1:
        return None
    b = data[tokpos + 1:end_nul]
    if 0 in b:
        return None
    try:
        s = b.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return s if len(s) <= 500 else None


def _try_utf16(data: bytes, tokpos: int, end_nul: int) -> Optional[str]:
    """UTF-16 FText source: token at tokpos, ends with NUL NUL at end_nul-1,end_nul."""
    if tokpos < 0:
        return None
    b = data[tokpos + 1:end_nul + 1]
    if len(b) < 4 or len(b) % 2:
        return None
    if not (b[-1] == 0 and b[-2] == 0):
        return None
    body = b[:-2]
    if len(body) % 2:
        return None
    try:
        s = body.decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    if "\x00" in s or len(s) > 500:
        return None
    return s


def _pattern_a_source(data: bytes, i: int) -> Optional[str]:
    """Given a key at [i..i+32) with a token before it, recover the source before the key."""
    if i < 1 or i + 33 > len(data):
        return None
    if data[i + 32] != 0 or data[i - 1] not in (TOK_ASCII, TOK_UNICODE):
        return None
    src_end = i - 2  # position of the source's terminating NUL
    lo = max(0, src_end - 2048)
    seg = data[lo:src_end]
    cands = []
    for tb in (TOK_ASCII, TOK_UNICODE):
        idx = seg.rfind(bytes([tb]))
        while idx >= 0:
            cands.append((idx, tb))
            idx = seg.rfind(bytes([tb]), 0, idx)
    cands.sort(key=lambda x: -x[0])  # closest to the key first
    for (idx, tb) in cands:
        tokpos = lo + idx
        s = _try_ascii(data, tokpos, src_end) if tb == TOK_ASCII else _try_utf16(data, tokpos, src_end)
        if s is not None and s.strip():
            return s
    return None


def _pattern_b_source(data: bytes, i: int) -> Optional[str]:
    """Given a key at [i..i+32) preceded by [i32 33], read the source FString after it."""
    if i < 4 or i + 33 > len(data):
        return None
    if data[i + 32] != 0:
        return None
    if struct.unpack_from("<i", data, i - 4)[0] != 33:
        return None
    if i + 37 > len(data):
        return None
    slen = struct.unpack_from("<i", data, i + 33)[0]
    if slen is None:
        return None
    # ASCII source
    if 0 < slen <= 8192 and i + 37 + slen <= len(data):
        b = data[i + 37:i + 37 + slen]
        if b[-1:] == b"\x00" and 0 not in b[:-1]:
            try:
                return b[:-1].decode("utf-8")
            except UnicodeDecodeError:
                return None
    # UTF-16 source (negative length = -2 * char_count, incl. terminator)
    if slen < 0 and slen % 2 == 0:
        need = (-slen // 2) * 2
        if i + 37 + need <= len(data):
            b = data[i + 37:i + 37 + need]
            try:
                s = b[:-2].decode("utf-16-le")
                if 0 < len(s) <= 500 and "\x00" not in s:
                    return s
            except UnicodeDecodeError:
                return None
    return None


def extract_from_bytes(data: bytes,
                       key_filter: Optional[set] = None) -> dict:
    """
    Extract {key: {source: count}} from a single asset's raw bytes.

    :param data: raw .uasset/.uexp bytes
    :param key_filter: if given, only record keys present in this set (speeds up)
    :return: dict key -> Counter(source -> occurrences)
    """
    found: dict = defaultdict(Counter)
    for m in _HEX32.finditer(data):
        i = m.start()
        # avoid matching a substring of a longer hex run
        if i > 0 and data[i - 1:i] in _HEXBYTES:
            continue
        k = m.group(0).decode("ascii").upper()
        if key_filter is not None and k not in key_filter:
            continue
        # Pattern A (bytecode)
        src = _pattern_a_source(data, i)
        if src is not None:
            found[k][src] += 1
            continue  # a key in bytecode context is authoritative here
        # Pattern B (map)
        src = _pattern_b_source(data, i)
        if src is not None:
            found[k][src] += 1
    return found


def resolve(found: dict) -> dict:
    """Reduce {key: Counter(src) | {src: count}} -> {key: most-common-src}."""
    out = {}
    for k, c in found.items():
        if hasattr(c, "most_common"):
            out[k] = c.most_common(1)[0][0]
        else:
            out[k] = max(c.items(), key=lambda x: x[1])[0]
    return out


def iter_pak_files(pak, prefixes: Iterable[str] = ("SurrounDead/",),
                   extensions: tuple = (".uasset", ".uexp")) -> Iterator[str]:
    """Yield in-pak file paths matching the given prefixes/extensions."""
    pre = tuple(prefixes)
    for path in pak.list_files():
        if any(path.startswith(p) for p in pre) and path.endswith(extensions):
            yield path
