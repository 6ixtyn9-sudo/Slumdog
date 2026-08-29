# Milestone 7B — Verifiable Full-Payload Shadow Bundle

**Status:** IMPLEMENTED AND TESTED LOCALLY (synthetic fixtures only).
**No real shadow run, real Forebet capture, or real bundle has been created.**
**Production NOT authorized. Shortlist policy NOT authorized. Training FROZEN.**

This milestone removes the final durability blocker before the first genuine
forward shadow run: it provides a small, standard-library-only tool that
packages a *completed* Milestone 7 shadow run and every input needed to
audit or reproduce it into a deterministic, independently verifiable
full-payload archive.

The tool is **post-decision preservation only**. It never reruns R2, changes
ranking or selections, alters the payload/manifest, attaches outcomes, reads
settlement results to modify the bundle, uses odds, invokes Forebet or
collection, or performs any production/training action. It only reads the
exact bytes the completed run already committed to.

- Module: `src/slumdog/shadow_bundle.py`
- Tests: `tests/test_shadow_bundle.py` (67 focused tests, including bounded-memory streaming)
- Standard library only; the module imports **no** other Slumdog submodule, so
  verification can run on an independent machine with nothing but the archive,
  the receipt, and Python.

---

## 1. Commands

### Create

```bash
python -m slumdog.shadow_bundle create \
  --run-dir data/reports/shadow/<target-date>/<run-id> \
  --output-dir <explicit-output-directory> \
  --root <repository-root>
```

- `--run-dir` — a completed run directory containing `manifest.json` (the
  completion marker) and `shadow_selections.json`.
- `--output-dir` — **explicit** destination for the bundle triplet. Created if
  absent. It must live inside an approved root or be explicitly supplied by
  the operator. Existing final outputs always cause failure (no force).
- `--root` — approved repository root containing `config/` and `data/`.

On success it writes three files (`0600` mode), in order:

```text
slumdog-shadow-<target-date>-<run-id>.tar.gz
slumdog-shadow-<target-date>-<run-id>.bundle.json
slumdog-shadow-<target-date>-<run-id>.tar.gz.sha256
```

### Verify (in memory, never extracts)

```bash
python -m slumdog.shadow_bundle verify \
  --bundle slumdog-shadow-<target-date>-<run-id>.tar.gz \
  --receipt slumdog-shadow-<target-date>-<run-id>.bundle.json
```

Exit code `0` prints `BUNDLE_VERIFIED`; any integrity failure prints
`BUNDLE_VERIFY_FAILED: <reason>` to stderr and exits `2` **without a Python
traceback**. If a sibling `.sha256` marker is present it is checked too.

---

## 2. Bundle contents

A completed run plus exactly the evidence it references — nothing else:

```text
bundle/
  run/
    shadow_selections.json     # exact original payload bytes
    manifest.json              # exact original manifest bytes (completion marker)
  config/
    research_baselines_v1.json # frozen R2 rule/config
    shadow_evaluator_v1.json   # shadow declaration
  capture/
    receipt.json               # capture receipt used by the run
    sidecars/<sha256>.json     # every referenced capture sidecar
    bodies/<sha256>.txt        # every referenced raw capture body
  history/
    <sha256>.<ext>             # every referenced history input (.jsonl.gz / .json)
  inventory.json               # canonical content-addressed inventory
  README.txt                   # human-readable verification instructions
```

All `capture/` and `history/` file names are content-addressed by exact-byte
SHA-256. No host-specific or absolute paths appear inside the archive;
original repository-relative paths are recorded in `inventory.json`.

### Inventory schema

`bundle/inventory.json` (canonical JSON: sorted keys, compact separators):

```json
{
  "bundle_schema_version": "slumdog_shadow_bundle_v1",
  "run_schema_version": "shadow_evaluator_v1",
  "run_id": "<16-hex>",
  "target_date": "YYYY-MM-DD",
  "content_member_count": <int>,
  "members": [
    {
      "archive_member": "bundle/capture/bodies/<sha>.txt",
      "role": "capture_body",
      "sha256": "<exact-byte sha256 of the member>",
      "bytes": 123,
      "source_paths": ["data/raw/football/<date>/<stamp>_<sha12>.txt"]
    }
  ]
}
```

Every content member (including `README.txt`) has one row mapping:

- `role` — run_payload, run_manifest, frozen_baseline_config,
  shadow_declaration, capture_receipt, capture_sidecar, capture_body,
  history_input, or bundle_readme;
- `source_paths` — the original repository-relative path(s) (empty for the
  generated README);
- `sha256` / `bytes` — exact-byte SHA-256 and size of the member.

The inventory deliberately does **not** list itself (a document cannot record
its own hash). Its SHA-256 and size are recorded in the external bundle
receipt. Identical bytes referenced from multiple original paths are
deduplicated to one content-addressed member, while **all** original
references remain represented in `source_paths`.

### Bundle receipt (`*.bundle.json`)

Records the externally-facing identity of the whole bundle:

- bundle schema/version and run schema/version;
- target date, run id, run status;
- archive filename, exact archive SHA-256 and byte size;
- source manifest SHA-256 and source payload SHA-256;
- frozen baseline config canonical SHA-256 and shadow declaration canonical
  SHA-256;
- input digest, decision digest, decision commit timestamp, safe cutoff;
- bundle creation timestamp (**excluded from archive content identity**);
- inventory SHA-256/size, member count, total uncompressed bytes;
- `durability_status = "LOCAL_EXPORT_READY_FOR_INDEPENDENT_COPY"`;
- explicit flags, all `false`: `production_authorized`,
  `shortlist_policy_authorized`, `training_authorized`,
  `threshold_optimization_authorized`.

---

## Bounded-memory streaming (verifier reliability)

The tool must stay reliable precisely when its input grows or is malformed,
so it never allocates an entire file or archive.

- **Create:** small metadata files (payload, manifest, both configs, capture
  receipt, sidecars, inventory, README) are read in memory only under a
  metadata byte cap. Potentially large evidence — raw capture bodies and
  history inputs — is verified and archived **by streaming**. Each evidence
  file is opened once, `fstat`-ed, hashed in fixed chunks, rewound, and
  streamed directly into the tar; it is re-hashed during tar streaming and its
  size/inode/mtime identity is re-checked on close, so the bytes that enter the
  archive are exactly the bytes that were verified (no check/use race). The
  archive is written to a temp file and then stream-hashed; it is never held in
  memory.
- **Verify:** the compressed archive is read through a counting/hashing
  wrapper (so the archive SHA-256 and compressed size accumulate as tarfile
  streams the gzip data). Two streaming passes are made: pass 1 hashes and
  sizes **every** member in bounded chunks (discarding the bytes), checking
  name/type/size first; pass 2 buffers and parses only the small metadata
  members needed for semantic checks — again under the metadata cap. Nothing
  is extracted to disk.

Explicit limits (constants in `shadow_bundle.py`; there is **no**
`--unlimited` / override):

| Limit | Value | Rationale |
|---|---|---|
| Stream chunk | 1 MiB | bounded read/write granularity |
| Max compressed archive | 512 MiB | comfortably above the current ~53 MB retained data |
| Max single evidence member | 256 MiB | bounds any one raw body / history file |
| Max total uncompressed | 1 GiB | bounds the decompressed bundle |
| Max metadata member | 16 MiB | JSON config/manifest/inventory are small |
| Max member count | 10,000 | bounds archive metadata |
| Max member path length | 512 bytes | path-safety guard |

These sit far above the current retained dataset but far below any accidental
multi-gigabyte allocation. An oversized member is rejected when its tar header
is read — **before** any content is allocated.

## 3. Deterministic archive rules

`.tar.gz` produced with the Python standard library only (no shelling out):

- stable member ordering (sorted logical path);
- normalized archive paths (logical `bundle/...` layout, no absolute paths);
- fixed tar metadata: UID/GID `0`, empty owner/group names, mode `0644`,
  mtime `0`;
- `ustar` format, streamed (`w|`) into a gzip wrapper;
- gzip timestamp fixed to `0`, fixed compression level, no embedded original
  filename;
- regular files only — no symlinks, hard links, devices, FIFOs, sockets,
  directories, or PAX extensions.

Same verified source bytes → identical archive bytes and identical archive
SHA-256, regardless of host, working directory, or wall-clock time. The
creation timestamp appears only in the external receipt and never affects
archive bytes. Determinism is verified by creating the same bundle twice into
separate output directories and comparing with exact-byte comparison /
`sha256sum`.

---

## 4. What the verifier checks (all in memory)

1. Archive exact-byte SHA-256 against the receipt (and sibling marker, if any).
2. Receipt schema, bundle version, durability status, and that all four
   authorization flags are `false`.
3. Every archive member is a regular file (rejects symlinks, hard links,
   devices, FIFOs, directories, unsupported member types).
4. No absolute paths, no `..` traversal, no backslashes, every member under
   the `bundle/` prefix.
5. No duplicate member names.
6. Inventory schema; inventory SHA-256/size against the receipt.
7. Every inventory member's SHA-256 and byte size against the actual bytes.
8. Detects members listed in the inventory but missing from the archive, and
   archive members not listed in the inventory (unexpected).
9. Bundled payload SHA-256 against the bundled run manifest.
10. Recomputes the frozen baseline config canonical SHA-256 from the bundled
    bytes (must equal the frozen constant) and the shadow declaration canonical
    SHA-256 (must match the manifest and receipt).
11. Recomputes the input digest, decision digest, and run id from the bundled
    provenance and checks run id / target date consistency across payload,
    manifest, and receipt.
12. Fail-closed authorization flags on the bundled declaration are all `false`.

Verification never extracts files to disk (`tarfile.extract`/`extractall` are
not used; members are read into memory and hashed).

---

## 5. Refusal behavior (create-time, fail closed)

The create command refuses, without writing a bundle, when:

- the run directory is missing `manifest.json` (partial run) or
  `shadow_selections.json`;
- the run is a blocked receipt (`run_status = SHADOW_RUN_BLOCKED` or
  `run_id = BLOCKED`);
- the payload or manifest is corrupt/not JSON;
- the payload's exact-byte SHA-256 does not match `manifest.payload_file_sha256`;
- the run schema/version is unsupported;
- the run directory or any referenced path escapes the approved repository
  root, is absolute-outside-root, uses `..` traversal, or is reached through a
  symlink;
- a referenced capture receipt, sidecar, raw body, or history input is missing
  or not a regular file;
- any referenced input's exact-byte SHA-256 (or recorded size) does not match
  the manifest provenance;
- the frozen baseline config canonical SHA-256 or shadow declaration canonical
  SHA-256 do not match;
- any authorization gate in the declaration is not `false`;
- an output archive/receipt/checksum path already exists (no overwrite).

---

## 6. Atomic finalization / no overwrite

- Output directory is explicit; all three final paths are pre-checked and any
  collision fails before anything is written. There is no force/overwrite flag.
- Each file is written to a `0600` temporary sibling, fsynced, and atomically
  renamed into place (`os.replace`), then chmod `0600`.
- Finalization order: the archive is written to a temp sibling, stream-hashed,
  and then **self-verified with the same full streaming verifier against the
  expected receipt contract**; only after self-verification succeeds is the
  archive atomically renamed into place, followed by the receipt and finally
  the checksum marker. Successful tar writing alone is never trusted.
- An interruption or failed self-verification leaves visibly incomplete output
  (no valid receipt/checksum; the temp archive is removed), which can never
  verify as a complete bundle.
- Source evidence is read-only; inputs are never modified or redacted.

---

## 7. Independent local verification (owner procedure)

After downloading **both** the archive and the bundle receipt (and optionally
the `.sha256` marker) to a local computer:

```bash
# 1. The printed SHA-256 must equal the receipt's "archive_sha256"
#    (and the value in <archive>.sha256).
sha256sum slumdog-shadow-<target-date>-<run-id>.tar.gz

# 2. If the Slumdog package is available (full verification, no extraction):
python -m slumdog.shadow_bundle verify \
  --bundle slumdog-shadow-<target-date>-<run-id>.tar.gz \
  --receipt slumdog-shadow-<target-date>-<run-id>.bundle.json
#    -> exit 0 and BUNDLE_VERIFIED
```

The first real shadow prediction is considered **backed up only when all four
hold**:

1. the Codespace source artifact exists and verifies;
2. a downloaded archive exists independently on another machine;
3. the local archive SHA-256 matches the Codespace bundle receipt;
4. full bundle verification exits zero on the independent machine.

**A hash without the full archive is not a second copy.**

---

## 8. Durability boundary and non-production status

Durability status on a created bundle is
`LOCAL_EXPORT_READY_FOR_INDEPENDENT_COPY`: the export is a portable,
independently copyable artifact, but it is not a second backup until an
independently downloaded full archive verifies on a separate machine.

This tool authorizes nothing. Production publication, shortlist policy,
training, threshold optimization, real-money use, and outcome attachment are
**not** authorized and are **not** performed.

### Remaining blockers to the first real run

1. Synchronize the data-bearing Codespace and create/verify/download a
   **synthetic** bundle there to prove the transport procedure end to end.
2. Authorize one gentle future-date football capture.
3. Generate the first real shadow artifact, bundle it immediately, download
   and independently verify it (the four conditions above).
4. Only then open the shadow report to see whether R2 emitted picks or
   honestly abstained.
