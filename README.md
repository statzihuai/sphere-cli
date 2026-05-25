# SPHERE CLI

Command-line interface for **SPHERE** — synthetic data generation, evaluation, and certification. Designed for business workflows, data pipelines, and HPC environments.

> For the desktop application (individual users), see [SPHERE App](https://github.com/statzihuai/SPHERE).

---

## Install

**npm (recommended):**

```sh
npm install -g sphere-cli
```

No Python, no curl, no PATH editing. Requires Node.js ≥ 16.

**curl (no Node.js required):**

```sh
curl -fsSL https://github.com/statzihuai/sphere-cli/releases/latest/download/install.sh | sh
```

For HPC / cloud with no sudo:

```sh
curl -fsSL https://github.com/statzihuai/sphere-cli/releases/latest/download/install.sh | sh -s -- --prefix ~/.local
# then add to ~/.bashrc or ~/.zshrc:
export PATH="$HOME/.local/bin:$PATH"
```

**Uninstall:**

```sh
sh install.sh --uninstall
```

### Supported platforms

| Platform | Architecture |
|---|---|
| macOS | Apple Silicon (arm64) |
| Linux | x86\_64 |
| Linux | arm64 (AWS Graviton, etc.) |

---

## Quick start

```sh
# Try the built-in demo (no data needed)
sphere demo

# Activate your license (once)
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

On the very first invocation the CLI cold-loads its bundled Python libraries (pandas, pyarrow, anonymeter, sklearn) from disk. On Apple Silicon this typically takes **15–25 seconds** and is shown in the progress bar as each library finishes:

```
Generating synthetic data from nhanes_sample.csv …
  [░░░░░░░░░░░░░░░░░]   0.0%  loading pandas . .
  [█░░░░░░░░░░░░░░░░]   3.0%  ✓ pandas  (12.4 s)
  [██░░░░░░░░░░░░░░░]   6.0%  ✓ pyarrow  (3.1 s)
  [███░░░░░░░░░░░░░░]   9.0%  ✓ sphere core  (1.8 s)
  …
✓ synth.csv  4,899 rows × 18 cols  (load 17.4 s + run 1.8 s)  seed 3721018536
```

Subsequent calls in the same session skip loading entirely. The timing line always shows **load** (library startup) and **run** (actual SPHERE computation) separately so you can see which part is slow.

> Exact times vary by machine, OS page cache state, and whether the binary has been run recently.

---

## Commands

### `sphere demo`

Run SPHERE end-to-end on the built-in NHANES sample dataset (4,899 rows × 18 columns, mix of continuous and categorical variables). No data or license required — good for testing an installation.

```sh
sphere demo
```

```
SPHERE demo — built-in NHANES dataset (4,899 rows × 18 cols, continuous + categorical)
────────────────────────────────────────────────────

Generating synthetic data from nhanes_sample.csv …
  [░░░░░░░░░░░░░░░░░]   0.0%  loading pandas . .
  [█░░░░░░░░░░░░░░░░]   3.0%  ✓ pandas  (12.4 s)
  [██░░░░░░░░░░░░░░░]   6.0%  ✓ pyarrow  (3.1 s)
  [███░░░░░░░░░░░░░░]   9.0%  ✓ sphere core  (1.8 s)
  [████████████████░]  85.0%  writing output
✓ /tmp/synth.csv  4,899 rows × 18 cols  (load 17.4 s + run 1.8 s)  seed 3721018536

Evaluating nhanes_sample.csv vs synth.csv …
  [████░░░░░░░░░░░░░]  16.0%  loading anonymeter . .
  [████░░░░░░░░░░░░░]  17.0%  ✓ anonymeter  (3.2 s)
  [█████░░░░░░░░░░░░]  18.0%  ✓ sklearn  (0.8 s)
  [█████████████████]  89.0%  inference  9/9
✓ Evaluation complete  (load 4.0 s + run 14.2 s)

  Fidelity
  ────────────────────────────────────
  Mean           100.0  ████████████████████
  Variance        99.7  ████████████████████
  Correlation     95.1  ███████████████████░
  KS              96.8  ███████████████████░
  ────────────────────────────────────
  Composite       97.9  ████████████████████

  Privacy
  ────────────────────────────────────
  Singling Out   100.0  ████████████████████
  Linkability     97.5  ███████████████████░
  Inference       96.8  ███████████████████░
  ────────────────────────────────────
  Composite       98.1  ████████████████████
```

---

### `sphere license`

Activate and manage your SPHERE license. A valid license is required to use `generate`, `evaluate`, and `certify`.

```
sphere license activate [KEY]   # Activate with a sphere_… key (prompts if omitted)
sphere license status           # Check current license (validates online, falls back to cache)
sphere license clear            # Remove stored key and cache
```

The key is stored at `~/.config/sphere/license_key` (mode 0600). After a successful activation the license is cached locally for **7 days**, so the CLI works offline within that window.

> Don't have a license? Contact [zihuai@stanford.edu](mailto:zihuai@stanford.edu) or visit [sphere.stanford.edu](https://sphere.stanford.edu).

---

### `sphere generate`

```
sphere generate <real.csv> [options]

Options:
  -o, --output PATH        Output CSV path (default: <input>_sphere.csv)
  -n, --rows INT           Number of synthetic rows (default: same as input)
  -k INT                   Synthesis depth (default: 2)
  --seed INT               Random seed for reproducibility
  --mix-prob FLOAT         Mixture probability 0–1 (default: 0.75)
  --json                   Machine-readable JSON output
```

A `.sphere.json` provenance file is written alongside every output CSV and is automatically read by `sphere certify`.

---

### `sphere evaluate`

```
sphere evaluate <real.csv> <synth.csv> [options]

Options:
  --skip-privacy           Skip privacy metrics (faster)
  --seed INT               Fix the random seed for reproducible attack results
  --json                   Machine-readable JSON output
```

Reports four fidelity metrics (mean, variance, correlation, KS) and three privacy metrics (singling-out, linkability, inference), each scored 0–100. Scores are normalised against a column-shuffled baseline so 100 = no measurable privacy leakage relative to a random permutation of the data.

---

### `sphere certify`

```
sphere certify <real.csv> <synth.csv> [options]

Options:
  -o, --output PATH        Output HTML report path (default: cert.html)
  --json                   Machine-readable JSON output
```

Produces a self-contained HTML certificate with fidelity and privacy scores, dataset metadata, and generation provenance. Generation parameters (`k`, `seed`, `theta`, etc.) are loaded automatically from the `.sphere.json` sidecar; pass flags explicitly to override.

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
| `SPHERE_WORKER_URL` | Override the license validation endpoint |
| `SPHERE_PREFIX` | Override install prefix |
| `SPHERE_VERSION` | Pin a release tag, e.g. `v0.1.38` |
| `SPHERE_BUNDLE_URL` | Full URL to a `sphere-cli-*.tar.gz` (skip auto-detect) |
| `SPHERE_GITHUB_REPO` | Override GitHub repo for downloads |

---

## License

Proprietary — see [LICENSE](LICENSE).
