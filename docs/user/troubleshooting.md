# Troubleshooting

## Mirror and Indexer Diagnostics

### Inspecting metadata files in the mirror database

After running `debcraft mirror sync`, you can query `mirror.db` directly to
see which repository metadata files (Packages, Sources, Release, Contents)
were downloaded and their current state. This is useful when `debcraft index`
reports "0 packages indexed" — it helps determine whether the metadata files
are present and in the expected state.

```bash
python3 -c "
import sqlite3
from debcraft.infrastructure.storage.paths import resolve_xdg_path

db = sqlite3.connect(str(resolve_xdg_path('database') / 'mirror.db'))
rows = db.execute('''
    SELECT url, state FROM repository_files
    WHERE url LIKE '%Packages%'
       OR url LIKE '%Sources%'
       OR url LIKE '%Release%'
       OR url LIKE '%Contents%'
''').fetchall()
for url, state in rows:
    print(f'{state}: {url}')
"
```

**Expected output** shows metadata files in `VERIFIED` or `INDEXED` state:

```
INDEXED: https://mirror.elxr.dev/elxr/dists/aria/InRelease
INDEXED: https://mirror.elxr.dev/elxr/dists/aria/main/binary-amd64/Packages.gz
INDEXED: https://mirror.elxr.dev/elxr/dists/aria/main/binary-arm64/Packages.gz
```

**If no rows are returned**, the mirror sync did not download the metadata
index files. Verify your repository configuration and re-run
`debcraft mirror sync`.

### Understanding "Skipping unknown file type" messages

When running `debcraft index` with verbose logging, you may see many lines like:

```
DEBUG debcraft.domain.indexer.service: Skipping unknown file type: https://mirror.elxr.dev/elxr/pool/main/z/zlib/zlib1g_1.2.13-1_amd64.deb
```

This is **normal behavior**. The indexer only processes repository metadata
files (Packages, Sources, Contents, Release). It does not parse `.deb` files
directly — package metadata is extracted from the index files that *describe*
those `.deb` files.

### Forcing a full re-index

The indexer uses incremental logic: if a metadata file has the same SHA256 as
the last time it was indexed, it is skipped. To force a complete re-index,
delete the metadata database and re-run:

```bash
rm "$(python3 -c 'from debcraft.infrastructure.storage.paths import resolve_xdg_path; print(resolve_xdg_path("database") / "metadata.db")')"
uv run debcraft index
```

Expected output after a fresh re-index:

```
                         Indexing Summary
┏━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Repository ┃ Packages ┃ Source Pkgs ┃ File Ownerships ┃ Skipped ┃ Status ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ elxr       │    29318 │           0 │               0 │       0 │ OK     │
└────────────┴──────────┴─────────────┴─────────────────┴─────────┴────────┘
```

### Checking overall mirror status

```bash
uv run debcraft mirror status
```

This shows how many files are cached, any failures, cache size, and last sync
timestamp. Example output:

```
                   Mirror Status
┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Metric                  ┃ Value                      ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Configured repositories │ 1                          │
│ Last sync               │ 2026-08-11 01:55:53.288444 │
│ Cached files            │ 29329                      │
│ Failed files            │ 0                          │
│ Cache size              │ 71.2 GiB                   │
└─────────────────────────┴────────────────────────────┘
```

## Common Scenario: "0 packages indexed" After Sync

### Symptom

After running the full workflow:

```bash
uv run debcraft mirror sync
uv run debcraft mirror verify
uv run debcraft index
```

The indexer reports "0 packages indexed" and shows many "Skipping unknown file
type" debug messages for `.deb` URLs like:

```
DEBUG debcraft.domain.indexer.service: Skipping unknown file type: https://mirror.elxr.dev/elxr/pool/main/z/zxing-cpp/libzxing-dev_1.4.0-3+b1_amd64.deb
DEBUG debcraft.domain.indexer.service: Skipping unknown file type: https://mirror.elxr.dev/elxr/pool/main/z/zziplib/libzzip-0-13_0.13.72+dfsg.1-1.1_amd64.deb
```

The summary table shows:

```
┏━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Repository ┃ Packages ┃ Source Pkgs ┃ File Ownerships ┃ Skipped ┃ Status ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ elxr       │        0 │           0 │               0 │       3 │ OK     │
└────────────┴──────────┴─────────────┴─────────────────┴─────────┴────────┘
```

### Explanation

This is expected on a **second or subsequent run**. The indexer uses
incremental logic — it tracks which files have already been indexed (by SHA256
and parser version). On the first run, it parses the `Packages.gz` and other
metadata files and stores the results in `metadata.db`. On subsequent runs, if
those files haven't changed, it skips them.

The "Skipping unknown file type" messages for `.deb` files are also normal.
The indexer extracts package metadata from the repository index files
(`Packages.gz`, `Sources.gz`, `Contents-*.gz`, `InRelease`), not from `.deb`
archives directly.

### Diagnosis

Run the metadata file query to confirm the state:

```bash
python3 -c "
import sqlite3
from debcraft.infrastructure.storage.paths import resolve_xdg_path

db = sqlite3.connect(str(resolve_xdg_path('database') / 'mirror.db'))
rows = db.execute('''
    SELECT url, state FROM repository_files
    WHERE url LIKE '%Packages%'
       OR url LIKE '%Sources%'
       OR url LIKE '%Release%'
       OR url LIKE '%Contents%'
''').fetchall()
for url, state in rows:
    print(f'{state}: {url}')
"
```

If you see output like:

```
INDEXED: https://mirror.elxr.dev/elxr/dists/aria/InRelease
INDEXED: https://mirror.elxr.dev/elxr/dists/aria/main/binary-amd64/Packages.gz
INDEXED: https://mirror.elxr.dev/elxr/dists/aria/main/binary-arm64/Packages.gz
```

Then **your data is already indexed**. The metadata files were successfully
parsed in a prior run. The "3 files skipped" in the summary corresponds to
these three metadata files being skipped by the incremental logic.

The `.deb` files in `VERIFIED` state (pool URLs) are artifacts downloaded by
the mirror — they are correctly skipped by the indexer since package metadata
comes from the index files, not from `.deb` archives.

### Verifying the indexed data

Confirm that package metadata is available:

```bash
uv run debcraft index package libc6
```

Expected output:

```
╭─────────────────────────── libc6 ───────────────────────────╮
│  Package         libc6                                      │
│  Version         2.36-9+deb12u14                            │
│  Architecture    arm64                                      │
│  Source          glibc                                      │
│  Source Version  2.36-9+deb12u14                            │
│  Section         libs                                       │
│  Priority        optional                                   │
│  Maintainer      GNU Libc Maintainers                       │
│                  <debian-glibc@lists.debian.org>            │
│  Homepage        https://www.gnu.org/software/libc/libc.h…  │
│  Description     GNU C Library: Shared libraries            │
│                  Contains the standard libraries that are   │
│                  used by nearly all programs on             │
│                  the system. This package includes shared   │
│                  versions of the standard C library         │
│                  and the standard mat...                    │
╰─────────────────────────────────────────────────────────────╯
```

### If you need a fresh re-index

Delete `metadata.db` to reset the incremental state and force a full re-parse:

```bash
rm "$(python3 -c 'from debcraft.infrastructure.storage.paths import resolve_xdg_path; print(resolve_xdg_path("database") / "metadata.db")')"
uv run debcraft index
```

This time you should see a non-zero package count matching the number of
entries in the `Packages.gz` files.
