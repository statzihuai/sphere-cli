# SPHERE CLI

Command-line interface for **SPHERE** — synthetic data generation, evaluation, and certification. Designed for business workflows, data pipelines, and HPC environments.

> For the desktop application (individual users), see [SPHERE App](https://github.com/statzihuai/SPHERE).

---

## Install

**One line — no Python or dependencies required:**

```sh
curl -fsSL https://github.com/statzihuai/sphere-cli/releases/latest/download/install.sh | sh
```

**User-local install (no sudo — works on HPC / cloud):**

```sh
curl -fsSL https://github.com/statzihuai/sphere-cli/releases/latest/download/install.sh | sh -s -- --prefix ~/.local
```

Then add to your shell startup file (`~/.bashrc` or `~/.zshrc`):

```sh
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
# Generate synthetic data
sphere generate real.csv -o synth.csv

# Evaluate fidelity and privacy
sphere evaluate real.csv synth.csv

# Generate a certification report (HTML)
sphere certify real.csv synth.csv -o report.html
```

---

## Commands

### `sphere generate`

```
sphere generate <real.csv> [options]

Options:
  -o, --output PATH        Output CSV path (default: <input>_sphere.csv)
  -n, --rows INT           Number of synthetic rows (default: same as input)
  -k INT                   SPHERE rotations (default: 2)
  --seed INT               Random seed for reproducibility
  --mix-prob FLOAT         Mixture probability 0–1 (default: 0.75)
  --json                   Machine-readable JSON output
```

A `.sphere.json` provenance file is written alongside every output CSV and is automatically read by `sphere certify`.

### `sphere evaluate`

```
sphere evaluate <real.csv> <synth.csv> [options]

Options:
  --skip-privacy           Skip privacy metrics (faster)
  --json                   Machine-readable JSON output
```

### `sphere certify`

```
sphere certify <real.csv> <synth.csv> [options]

Options:
  -o, --output PATH        Output HTML report path (default: cert.html)
  --json                   Machine-readable JSON output
```

Generation parameters (`k`, `seed`, `theta`, etc.) are loaded automatically from the `.sphere.json` sidecar. Pass flags explicitly to override.

---

## Machine-readable output

Every command supports `--json` for pipeline integration:

```sh
sphere generate real.csv -o synth.csv --json | jq .fidelity
sphere evaluate real.csv synth.csv --json > metrics.json
```

---

## Environment variables

| Variable | Description |
|---|---|
| `SPHERE_PREFIX` | Override install prefix |
| `SPHERE_VERSION` | Pin a release tag, e.g. `v0.1.0` |
| `SPHERE_BUNDLE_URL` | Full URL to a `sphere-cli-*.tar.gz` (skip auto-detect) |
| `SPHERE_GITHUB_REPO` | Override GitHub repo for downloads |

---

## License

Proprietary — see [LICENSE](LICENSE).
