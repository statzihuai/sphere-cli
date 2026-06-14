# SPHERE CLI

Command-line interface for **SPHERE** — synthetic data generation, evaluation, and certification. Designed for business workflows, data pipelines, and HPC environments.

> For the desktop application (individual users), see [SPHERE App](https://github.com/statzihuai/SPHERE).

---

## Install

```sh
npm install -g sphere-cli
```

Requires **Node.js ≥ 18**. No Python and no manual PATH editing — the install downloads a self-contained, signed binary and wires everything up for you.

**Install once, run anywhere (HPC).** The ~500 MB engine is **not** placed inside `node_modules`, so it never blows up a quota-limited home directory. On a cluster the installer auto-detects roomy shared storage (`$OAK`, `$SCRATCH`, `$WORK`, `$PROJECT`, `$GROUP_HOME`, …) and installs there; because that storage and your `~/.bashrc` are shared across every login and compute node, you install once and `sphere` works in every future session on every node — no reinstall. If the global `bin` isn't already on your `PATH`, the installer appends it to your shell rc automatically. To pin a location, set `SPHERE_HOME=/path/with/space` before installing.

**Update / uninstall:**

```sh
npm install -g sphere-cli      # update to the latest version
npm uninstall -g sphere-cli    # remove
```

**No Node.js?** Download the tarball for your platform from the [latest release](https://github.com/statzihuai/sphere-cli/releases/latest), extract it, and run `sphere-cli/sphere` directly (add it to your `PATH` if you like).

### Supported platforms

| Platform | Architecture |
|---|---|
| macOS | Apple Silicon (arm64) |
| macOS | Intel (x86\_64) |
| Linux | x86\_64 (glibc ≥ 2.17 — runs on CentOS 7 / RHEL 7 and newer, incl. most HPC clusters) |

---

## Quick start

```sh
# Try the built-in demo (no data or license needed)
sphere demo

# Activate your license (once; required for generate/evaluate/certify)
sphere license activate sphere_xxxxxxxxxxxxxxxxxxxx

# Generate synthetic data
sphere generate real.csv -o synth.csv

# Evaluate fidelity and privacy
sphere evaluate real.csv synth.csv

# Generate a certification report (HTML)
sphere certify real.csv synth.csv -o report.html
```

---

## First run

On the very first invocation the CLI cold-loads its bundled Python libraries (pandas, pyarrow, anonymeter, sklearn) from disk. On Apple Silicon this typically takes **15–25 seconds**, shown in the progress bar as each library finishes:

```
Generating synthetic data from nhanes_sample.csv …
  [░░░░░░░░░░░░░░░░░]   0.0%  loading pandas . .
  [█░░░░░░░░░░░░░░░░]   3.0%  ✓ pandas  (12.4 s)
  [██░░░░░░░░░░░░░░░]   6.0%  ✓ pyarrow  (3.1 s)
  [███░░░░░░░░░░░░░░]   9.0%  ✓ sphere core  (1.8 s)
  …
✓ synth.csv  4,899 rows × 18 cols  (load 17.4 s + run 1.8 s)  seed 3721018536
```

Subsequent calls on the same node skip loading (OS page cache). The timing line always shows **load** (library startup) and **run** (actual SPHERE computation) separately so you can see which part is slow.

> On a cluster, the engine lives on shared network storage; the first run on a fresh node re-pays that cold load. If you launch many `sphere` commands in one job and want each to start fast, the launcher transparently caches the engine to node-local disk (`$L_SCRATCH`/`$TMPDIR`) on network filesystems — set `SPHERE_NO_FAST=1` to disable.

---

## Commands

### `sphere demo`

Run SPHERE end-to-end on the built-in NHANES sample dataset (4,899 rows × 18 columns, mix of continuous and categorical variables). No data or license required — good for testing an installation.

```sh
sphere demo
```

### `sphere license`

Activate and manage your SPHERE license. A valid license is required to use `generate`, `evaluate`, and `certify` (but **not** `demo`).

```
sphere license activate [KEY]   # Activate with a sphere_… key (prompts if omitted)
sphere license status           # Check current license (validates online, falls back to cache)
sphere license clear            # Remove stored key and cache
```

The key is stored at `~/.config/sphere/license_key` (mode 0600). After a successful activation the license is cached locally for **7 days**, so the CLI works offline within that window.

> Don't have a license? Contact [zihuai@stanford.edu](mailto:zihuai@stanford.edu) or visit [sphere.stanford.edu](https://sphere.stanford.edu).

### `sphere generate`

```
sphere generate <real.csv> [options]

Options:
  -o, --output PATH        Output CSV path (default: <input>_sphere.csv)
  --k INT                  Synthesis passes (default: 2; more = stronger privacy)
  --mix-prob FLOAT         Privacy/utility trade-off, 0–1 (default: 0.75)
  --seed INT               Random seed for reproducibility
  --json                   Machine-readable JSON output
```

The synthetic output has the **same number of rows** as the input (SPHERE transforms the data in place). Integer-coded categorical columns (≤ 10 distinct values, e.g. 0/1 flags or small ordinal scales) are preserved as exact discrete values; continuous columns are transformed while preserving the covariance structure. A `.sphere.json` provenance file is written alongside every output CSV and is read automatically by `sphere certify`.

### `sphere evaluate`

```
sphere evaluate <real.csv> <synth.csv> [options]

Options:
  --skip-privacy           Skip privacy metrics (faster)
  --n-attacks INT          Anonymeter attacks per metric (default: 500)
  --n-secrets INT          Random secret columns per inference replicate (default: 5)
  --n-reps INT             Inference replicates to average (default: 10; more = tighter, slower)
  --n-neighbors INT        k for the linkability k-NN test (default: 1)
  --n-aux-cols INT         Feature columns for the linkability A/B split (default: 20)
  --seed INT               Fix the random seed for fully reproducible results
  --json                   Machine-readable JSON output
```

Reports four fidelity metrics (mean, variance, correlation, KS) and three privacy metrics (singling-out, linkability, inference), each scored 0–100. Scores are normalised against a column-shuffled baseline, so 100 = no measurable leakage relative to a random permutation. The **inference** score averages `--n-reps` independent replicates of the random secret-column sampling, which makes it stable run-to-run (raise `--n-reps` for an even tighter estimate, or pass `--seed` for an exactly reproducible audit).

### `sphere certify`

```
sphere certify <real.csv> <synth.csv> [options]

Options:
  -o, --output PATH        Output HTML report path (default: cert.html)
  --json                   Machine-readable JSON output
```

Produces a self-contained HTML certificate with fidelity and privacy scores, dataset metadata, and generation provenance. Generation parameters are loaded automatically from the `.sphere.json` sidecar; pass flags explicitly to override.

---

## Machine-readable output

Every command supports `--json` for pipeline integration:

```sh
sphere generate real.csv -o synth.csv --json | jq .seed
sphere evaluate real.csv synth.csv --json > metrics.json
sphere evaluate real.csv synth.csv --json | jq '.privacy.composite'
```

---

## Environment variables

| Variable | Description |
|---|---|
| `SPHERE_LICENSE_REQUIRED` | Set to `false` to bypass license checks (research / unlocked builds) |
| `SPHERE_HOME` | Install location for the engine (default: auto-detected roomy/HPC storage, else `~/.local/share`) |
| `SPHERE_NO_FAST` | Set to `1` to disable node-local caching of the engine on network filesystems |
| `SPHERE_FAST_DIR` | Override the node-local cache directory (default: `$L_SCRATCH`/`$TMPDIR`) |
| `SPHERE_NO_PATH_SETUP` | Set to `1` to skip auto-adding the `bin` dir to your shell rc |
| `SPHERE_BINARY_BASEURL` | Override the release base URL the engine downloads from (testing) |
| `SPHERE_SKIP_POSTINSTALL` | Set to `1` to skip the binary download during `npm install` (CI / offline) |

---

## License

Proprietary — see [LICENSE](LICENSE).
