<p align="center">
  <h1 align="center">pypaktolocres</h1>
</p>

<p align="center">
  <b>Stream a UE4/UE5 <code>.pak</code>, extract every FText English source string, and build a <code>.locres</code> — without unpacking the pak.</b>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12-blue.svg">
  <img alt="pyuepak" src="https://img.shields.io/badge/pyuepak-%E2%89%A50.2.8-green.svg">
  <img alt="pylocres" src="https://img.shields.io/badge/pylocres-0.1.9.2-green.svg">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-lightgrey.svg">
</p>

---

## Why does this exist?

Some shipped Unreal Engine games ship **no game-localization `.locres`** inside
their main pak. The English (source) strings are baked into the cooked assets —
inside Kismet bytecode (`EX_TextConst` / `LocalizedText`) and `FText`
key→source maps — not in any central table.

Recovering those source strings normally means a heavyweight toolchain
(`UAssetAPI` + a `.usmap`, or full unpack + per-asset parsing). This tool does
it in **pure Python** by scanning the raw bytes at the FText key pattern level,
so it is fast, dependency-light, and needs **no `.usmap` and no `.NET`**.

It was built for the **SurrounDead** localization workflow but works for any
cooked UE pak that stores FText keys in `.uasset` / `.uexp`.

## Highlights

- ⚡ **No full unpack** — files are streamed one at a time from the pak in
  memory (`pyuepak`). Nothing is written to disk except the output you ask for.
- 🔎 **Byte-level FText extraction** — two validated patterns (Kismet bytecode
  + `FText` map) with a 32-hex key context check to keep false positives low.
- 📦 **Proven `.locres` output** — writes the exact **Compact (v1)** format the
  game / a working mod expects (same magic, version byte, layout).
- 🔁 **Reference mode** — feed an existing `.locres` to reuse its namespace
  structure and `SourceStringHash` values, then fill in the EN sources and
  append any keys you didn't have before.
- 🐍 **Python API + CLI** — use it as a library or one-liner.

## How extraction works

Every `.uasset` / `.uexp` is scanned (raw bytes, no structural parsing) for a
32-hex FText **Key** that sits in a valid FText context, then its **source
string** is read from the bytes immediately around the key. Two independent
patterns are recognized. All offsets below use `i` = byte offset where the
32-hex key starts, so the key occupies `data[i : i+32]`.

### Key detection (common to both patterns)

```python
for m in re.compile(rb"[0-9A-Fa-f]{32}").finditer(data):
    i = m.start()
    if data[i-1] is a hex char:  skip      # part of a longer hex run
    key = data[i:i+32].upper()
```

A bare 32-hex run is only *accepted* when it is also placed in one of the two
FText layouts below. That context check is what keeps the false-positive rate
very low (most 32-hex runs in an asset are GUIDs, hashes or pointer values and
are never in either layout).

### FString refresher

UE stores strings as `FString`: `[i32 length][bytes][NUL]`. `length` is the
byte count **including** the terminator, *except* for UTF-16 where a **negative**
length means `-2 * (char_count + 1)`. Both patterns rely on reading these.

---

### Pattern A — Kismet bytecode FText (source **before** the key)

This is a cooked Kismet `EX_TextConst` / `LocalizedText` node. In bytecode the
source string appears **first**, then the key, then (optionally) the namespace.

```
... [EX_TextConst][count] [srcTok][SRC][00] [keyTok][KEY32][00] [nsTok][NS][00] ...
                             └────────────────────────────────────────┘
                             Pattern A region (this tool reads this)
```

| token | value | meaning |
|-------|-------|---------|
| `EX_TextConst` | `0x29` | the FText constant node |
| `EX_StringConst` | `0x1F` | ASCII FString (`srcTok` / `keyTok`) |
| `EX_UnicodeStringConst` | `0x34` | UTF-16 FString (`srcTok` / `keyTok`) |

**Recognition guards** (a key at `i` is Pattern A only if all hold):

```python
data[i+32] in (0,)                 # NUL right after the 32-hex key
 and data[i-1] in (0x1F, 0x34)      # a string token immediately precedes it
```

**Reading the source (it sits *before* the key):**

```
srcTok @ p        source starts at p+1
... [SRC bytes] ...
NUL  @ i-2        source terminator  (srcTok is at i-1)
```

```
src_end = i - 2                     # position of the source's terminating NUL
scan backward from src_end (≤ 2048 B) for a 0x1F / 0x34 token byte p
try candidates closest to the key first
  if p is 0x1F (ASCII):  src = data[p+1 : i-2]   (no NUL inside, valid UTF-8)
  if p is 0x34 (UTF-16): data[i-3]==0 and data[i-2]==0   # 00 00 terminator
                         src = data[p+1 : i-3]    (decode UTF-16LE)
```

A candidate is accepted only if it decodes cleanly, is non-empty (after strip)
and is ≤ 500 chars. The **closest** clean token wins.

**Worked example** — `BP_AnimalDog.uexp` (key `7CA5338145E07BF8CC4387901AB97CCD` → `Pet`):

```
6887  29            EX_TextConst
6888  01            (node source-count)
6889  1f            srcTok  = EX_StringConst (ASCII)
6890-6892  50 65 74 "Pet"
6893  00            source NUL   (src_end = i-2 = 6893)
6894  1f            keyTok  = EX_StringConst (ASCII)
6895-6926  37...34  "7CA5338145E07BF8CC4387901AB97CCD"
6927  00            key NUL      (data[i+32] = 0)
→ source = data[6890 : 6893] = "Pet"
```

---

### Pattern B — `FText` key→source map (source **after** the key)

This is the plain `FText` serialization used in many export/data-table layouts:
the key is written first (as a 33-byte ASCII FString), immediately followed by
its source string.

```
[i32 33]  [KEY32]  [00]  [i32 srcLen]  [SRC]  [00]
  key len    key      keyNUL    src len     SRC    srcNUL
```

**Recognition guards** (a key at `i` is Pattern B only if all hold):

```python
data[i+32] == 0                     # NUL right after the key
 and int32(data[i-4 : i]) == 33     # key is a 33-byte ASCII FString (32 hex + NUL)
```

**Reading the source (it sits *after* the key):**

```
slen = int32(data[i+33 : i+37])     # source FString length
src bytes start at i+37
```

- **ASCII** — `0 < slen ≤ 8192`:
  `data[i+37 : i+37+slen]` must end in `00` with no interior NUL and decode as
  UTF-8. Source = the string minus the trailing NUL.
- **UTF-16** — `slen < 0` and even:
  `chars = -slen/2` (incl. terminator), `need = chars*2` bytes;
  `data[i+37 : i+37+need]`, source = the bytes minus the trailing `00 00`,
  decoded as UTF-16LE, ≤ 500 chars.

**Worked example** — `Enum_SpawnType.uexp` (key `E08DE8094956F44DD079E5A978CDB5B0` → `Zombies`):

```
offset 32   21 00 00 00   int32 33  (key FString length)
offset 36   45 30 38 ...   "E08DE8094956F44DD079E5A978CDB5B0" (32 bytes)
offset 68   00              key NUL
offset 69   08 00 00 00   int32 8   (source FString length = 7 chars + NUL)
offset 73   5a 6f 6d 62 69 65 73   "Zombies"
offset 80   00              source NUL
→ source = data[73 : 80] minus NUL = "Zombies"
```

---

### Precedence & resolution

- For a **single key occurrence**, Pattern A is tried first; if it matches,
  Pattern B is skipped (bytecode context is authoritative).
- The **same key** can appear in many files/occurrences. Occurrences are
  accumulated into a per-key `Counter` of sources; the **most frequent** source
  wins on `resolve()`.

### Known limitations

- Heuristic, byte-pattern based. It covers the Kismet and `FText`-map cases
  but will miss a string that is (a) embedded only in the C++/EXE, or (b) stored
  in an unusual FText layout not matching either pattern.
- A key that genuinely maps to two different sources (rare) resolves to the
  most common one; the runner-up is discarded. Call
  `extract_pak(pak, raw=True)` to get the raw per-key counts
  `{key: {source: count}}` before resolution.
- The 2048-byte backward scan window in Pattern A is generous for observed
  assets; a source stored further from its key would be missed.

### Extending the extractor

- **New token value** (e.g. another `EX_*StringConst`): add it to
  `TOK_ASCII`/`TOK_UNICODE` sets in `_extract.py` and to the guard sets in
  `_pattern_a_source`.
- **New FText layout**: add a `_pattern_c_source(data, i)` function, call it in
  `extract_from_bytes` after A/B, return the source or `None`.
- **Key filter**: pass `key_filter={...}` to `extract_from_bytes` to restrict to
  known keys (faster when you only want a subset).
- To *modify* an asset (change a string and re-serialize with correct Kismet
  jump offsets) the byte approach is **not** sufficient — that requires a
  structural parser (e.g. `UAssetAPI` + a `.usmap`).

## Installation

```bash
git clone https://github.com/ke1ng0/pypaktolocres.git
cd pypaktolocres

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

Dependencies (installed automatically): `pyuepak`, `pylocres==0.1.9.2`,
`cryptography` (required by `pyuepak`).

## Quick start (CLI)

```bash
pypaktolocres path/to/Game-Windows.pak \
    -o Game_en.locres \
    --reference path/to/existing_Game_en.locres \
    --csv en_reference.csv
```

A full reference CSV with an empty `th_text` column is written so you can
translate it and build the target-language `.locres` next.

### Options

| flag | meaning |
|------|---------|
| `pak` | path to the `.pak` file (positional, required) |
| `-o, --out` | output `.locres` path (required) |
| `--reference` | existing `.locres` to reuse structure + `SourceStringHash` from |
| `--csv` | also write a `key,namespace,en_text,th_text` CSV |
| `--prefix` | only read in-pak paths starting with this (repeatable; default `SurrounDead/`) |
| `--ext` | file extensions to scan (repeatable; default `.uasset .uexp`) |
| `--ns` | namespace for new/fresh entries (default empty) |
| `--version` | output format: `compact` (v1, default) / `legacy` / `optimized` / `cityhash` |
| `--keep-unresolved` | keep the reference value for keys with no extracted source (default: leave them empty) |
| `-q, --quiet` | less progress output |

## Python API

```python
from pypaktolocres import pak_to_locres

# One-shot: pak -> .locres
pak_to_locres(
    pak="path/to/Game-Windows.pak",
    out="Game_en.locres",
    reference="path/to/existing_Game_en.locres",   # optional
    prefixes=("SurrounDead/",),                     # only game content
    progress=lambda done, total, keys: None,        # optional progress hook
)
```

Two-step (inspect / edit sources before building):

```python
from pypaktolocres import extract_pak
from pypaktolocres._build import build_locres, write_locres

sources = extract_pak("path/to/Game-Windows.pak")   # {key: EN source}

# Need the raw per-key source counts (to inspect ambiguous keys)?
# counts = extract_pak("path/to/Game-Windows.pak", raw=True)  # {key: {source: n}}

# ... modify `sources` here if you like (add, drop, correct) ...

locres = build_locres(sources, reference="path/to/existing_Game_en.locres")
write_locres(locres, "Game_en.locres")
```

## Output

- **`.locres`** in **Compact (v1)** format — structurally identical to a known
  working mod file (same 16-byte magic, version byte = `1`, string LUT layout).
- **Reference CSV** `key,namespace,en_text,th_text` — fill `th_text` to produce
  the target language.

## Verified result (SurrounDead)

| | value |
|---|---|
| files scanned | 19,906 |
| run time | ~74 s (single core, no unpack) |
| unique FText keys extracted | 7,237 |
| `.locres` entries | 7,311 (7,011 from reference + 300 added) |
| resolved EN | 7,237 |
| unresolved (left empty) | 74 |

## Project layout

```
pypaktolocres/
├── pypaktolocres/
│   ├── __init__.py    # public API: pak_to_locres, extract_pak, ...
│   ├── __main__.py    # CLI entry point
│   ├── _extract.py    # FText byte extraction + pak file iterator
│   └── _build.py      # .locres construction via pylocres
├── pyproject.toml
└── README.md
```

## Limitations & notes

- Extraction is **heuristic / byte-pattern based**. It is excellent for the
  Kismet + `FText`-map cases but does not decode every conceivable FText
  storage layout. If a string is missing, it is usually a C++/EXE-embedded
  string that never appears in the pak.
- `SourceStringHash` for **new** keys is set to UE's `FString` CRC32 of the EN
  source. Runtime lookup uses the `(namespace, key)` pair, so this value does
  not block display; it only affects stale-translation bookkeeping.
- The game's `Engine/...` assets are intentionally skipped by the default
  `--prefix`.

## Dependencies

| package | version | role |
|---------|---------|------|
| [pyuepak](https://github.com/ke1ng0/pyuepak) | ≥ 0.2.8 | pak reading (streaming) |
| [pylocres](https://pypi.org/project/pylocres/) | 0.1.9.2 | `.locres` read/write |
| cryptography | any recent | required by `pyuepak` |

## License

[MIT](LICENSE)
