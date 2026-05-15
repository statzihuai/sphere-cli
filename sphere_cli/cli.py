"""sphere — generate, evaluate, and certify synthetic tabular data.

Usage
-----
  sphere generate data.csv -o synth.csv [--k 2] [--seed 42]
  sphere evaluate real.csv synth.csv [--skip-privacy] [--json]
  sphere certify  real.csv synth.csv -o certificate.html
  sphere certify  real.csv synth.csv -o certificate.html \\
                  --k 2 --theta 0.524 --seed 42   # marks as SPHERE-generated

All scores are on a 0–100 scale (higher = better).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# ── Progress bar (no external dependencies) ──────────────────────────────────

_BAR_WIDTH = 28

def _bar(frac: float, msg: str) -> str:
    filled = round(_BAR_WIDTH * min(frac, 1.0))
    bar    = "█" * filled + "░" * (_BAR_WIDTH - filled)
    return f"\r  [{bar}] {frac * 100:5.1f}%  {msg:<45}"

def _clear_line() -> None:
    print(f"\r{' ' * (_BAR_WIDTH + 60)}\r", end="", flush=True)


# ── generate ──────────────────────────────────────────────────────────────────

def cmd_generate(args: argparse.Namespace) -> int:
    _check_license()
    from ._generate import generate

    if not args.json:
        print(f"Generating synthetic data from {args.input} …")

    def on_progress(frac: float, msg: str) -> None:
        if not args.json:
            print(_bar(frac, msg), end="", flush=True)

    try:
        result = generate(
            input_path  = args.input,
            output_path = args.output,
            k           = args.k,
            theta       = args.theta,
            delta       = args.delta,
            mix_prob    = args.mix_prob,
            seed        = args.seed,
            on_progress = on_progress,
        )
    except ValueError as e:
        _clear_line()
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        _clear_line()
        import traceback
        print(f"Unexpected error: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result))
        return 0

    _clear_line()
    elapsed  = result["elapsedMs"] / 1000
    id_info  = f"  ID columns excluded: {result['idColName']}" if result["idColDetected"] else ""
    print(f"✓ {args.output}  {result['rows']:,} rows × {result['cols']} cols  "
          f"({elapsed:.1f} s)  seed {result['seed']}")
    if id_info:
        print(id_info)
    return 0


# ── evaluate ──────────────────────────────────────────────────────────────────

def _score_bar(score: float, width: int = 20) -> str:
    filled = round(width * score / 100)
    return "█" * filled + "░" * (width - filled)

def _print_eval_results(result: dict) -> None:
    fid  = result["fidelity"]
    priv = result.get("privacy")
    sep  = "─" * 36

    print()
    print("  Fidelity")
    print(f"  {sep}")
    for label, key in [("Mean", "meanScore"), ("Variance", "varScore"),
                       ("Correlation", "corScore"), ("KS", "ksScore")]:
        v = fid[key]
        print(f"  {label:<14} {v:5.1f}  {_score_bar(v)}")
    print(f"  {sep}")
    c = fid["composite"]
    print(f"  {'Composite':<14} {c:5.1f}  {_score_bar(c)}")

    if priv:
        print()
        print("  Privacy")
        print(f"  {sep}")
        for label, key in [("Singling Out", "singlingOut"),
                           ("Linkability", "linkability"),
                           ("Inference", "inference")]:
            v = priv[key]["score"]
            print(f"  {label:<14} {v:5.1f}  {_score_bar(v)}")
        print(f"  {sep}")
        c = priv["composite"]
        print(f"  {'Composite':<14} {c:5.1f}  {_score_bar(c)}")
    elif result.get("privacy") is None:
        print()
        print("  Privacy: skipped (--skip-privacy)")

    print()
    nR = result["nReal"]
    nS = result["nSynth"]
    p  = result["pOrig"]
    excluded = result.get("idColsExcluded", [])
    excl_note = f"  ({len(excluded)} ID cols excluded)" if excluded else ""
    print(f"  {nR:,} real rows × {p} cols  vs  {nS:,} synthetic rows{excl_note}")
    print()


def cmd_evaluate(args: argparse.Namespace) -> int:
    _check_license()
    from ._evaluate import evaluate

    if not args.json:
        print(f"Evaluating {args.real.name} vs {args.synth.name} …")

    t0 = time.perf_counter()

    def on_progress(frac: float, msg: str) -> None:
        if not args.json:
            print(_bar(frac, msg), end="", flush=True)

    try:
        result = evaluate(
            real_path    = args.real,
            synth_path   = args.synth,
            n_attacks    = args.n_attacks,
            n_secrets    = args.n_secrets,
            n_atk_cap    = args.n_atk_cap,
            n_neighbors  = args.n_neighbors,
            n_aux_cols   = args.n_aux_cols,
            seed         = args.seed,
            skip_privacy = args.skip_privacy,
            on_progress  = on_progress,
        )
    except ValueError as e:
        _clear_line()
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        _clear_line()
        import traceback
        print(f"Unexpected error: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    result["elapsedMs"] = int((time.perf_counter() - t0) * 1000)

    if args.json:
        print(json.dumps(result))
        return 0

    _clear_line()
    _print_eval_results(result)
    return 0


# ── certify ───────────────────────────────────────────────────────────────────

def cmd_certify(args: argparse.Namespace) -> int:
    _check_license()
    from ._evaluate import evaluate
    from ._certify  import build_certificate_html
    import numpy as np

    if not args.json:
        print(f"Certifying {args.real.name} vs {args.synth.name} …")

    t0 = time.perf_counter()

    def on_progress(frac: float, msg: str) -> None:
        if not args.json:
            print(_bar(frac, msg), end="", flush=True)

    try:
        result = evaluate(
            real_path    = args.real,
            synth_path   = args.synth,
            n_attacks    = args.n_attacks,
            n_secrets    = args.n_secrets,
            n_atk_cap    = args.n_atk_cap,
            n_neighbors  = args.n_neighbors,
            n_aux_cols   = args.n_aux_cols,
            seed         = args.seed,
            skip_privacy = args.skip_privacy,
            on_progress  = on_progress,
        )
    except ValueError as e:
        _clear_line()
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        _clear_line()
        import traceback
        print(f"Unexpected error: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    result["elapsedMs"] = int((time.perf_counter() - t0) * 1000)

    # ── Auto-load .sphere.json sidecar written by sphere generate ────────────
    # Look for <synth>.sphere.json next to the synthetic CSV.  Values found
    # there are used as defaults; any explicit CLI flag still overrides them.
    meta: dict = {}
    _meta_path = args.synth.with_suffix(".sphere.json")
    if _meta_path.exists():
        try:
            meta = json.loads(_meta_path.read_text(encoding="utf-8"))
            if not args.json:
                print(f"  (provenance loaded from {_meta_path.name})")
        except Exception:
            pass  # malformed sidecar — ignore silently

    # Merge: CLI flags win; sidecar fills the gaps; defaults last.
    _theta    = args.theta    if args.theta    is not None else meta.get("theta")
    _delta    = args.delta    if args.delta    is not None else meta.get("delta")
    _mix_prob = args.mix_prob if args.mix_prob is not None else meta.get("mix_prob")
    _k        = args.k        if args.k        is not None else meta.get("k")
    _seed_gen = args.seed_gen if args.seed_gen is not None else meta.get("seed")
    _gen_at   = args.generated_at if args.generated_at is not None else meta.get("generated_at")

    # Only populate gen_params when we actually know this was sphere-generated.
    gen_params = None
    if any(x is not None for x in [_theta, _delta, _mix_prob, _k, _seed_gen, _gen_at]):
        gen_params = {
            "theta":    _theta    if _theta    is not None else float(np.pi / 6),
            "delta":    _delta    if _delta    is not None else float(5 * np.pi / 180),
            "mix_prob": _mix_prob if _mix_prob is not None else 0.75,
            "k":        _k        if _k        is not None else 2,
            "seed":     _seed_gen,
        }

    _clear_line()

    html = build_certificate_html(
        result            = result,
        real_path         = args.real,
        synth_path        = args.synth,
        generation_params = gen_params,
        generated_at      = _gen_at,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")

    if args.json:
        print(json.dumps({"certificate": str(args.output), **result}))
        return 0

    fid  = result["fidelity"]
    priv = result.get("privacy")
    print(f"✓ {args.output}")
    print(f"  Fidelity  {fid['composite']:.1f} / 100", end="")
    if priv:
        print(f"   Privacy  {priv['composite']:.1f} / 100", end="")
    print(f"   ({result['elapsedMs'] / 1000:.1f} s)")
    print(f"  Open in a browser to view; File → Print → Save as PDF to archive.")
    return 0


# ── license ───────────────────────────────────────────────────────────────────

_LICENSE_WORKER_URL = os.environ.get(
    "SPHERE_WORKER_URL",
    "https://sphere-license.statzihuai.workers.dev",
)
_LICENSE_CACHE_DAYS = 1
_LICENSE_REQUIRED   = os.environ.get("SPHERE_LICENSE_REQUIRED", "true") != "false"


def _sphere_config_dir() -> Path:
    d = Path.home() / ".config" / "sphere"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _license_key_file()   -> Path: return _sphere_config_dir() / "license_key"
def _license_cache_file() -> Path: return _sphere_config_dir() / "license_cache.json"


def _read_stored_license_key() -> str | None:
    kf = _license_key_file()
    return kf.read_text(encoding="utf-8").strip() if kf.exists() else None


def _write_license_key(key: str) -> None:
    kf = _license_key_file()
    kf.write_text(key, encoding="utf-8")
    kf.chmod(0o600)


def _read_license_cache() -> dict | None:
    cf = _license_cache_file()
    if not cf.exists():
        return None
    try:
        data = json.loads(cf.read_text(encoding="utf-8"))
        age_days = (time.time() - data.get("cachedAt", 0)) / 86400
        return data if age_days <= _LICENSE_CACHE_DAYS else None
    except Exception:
        return None


def _write_license_cache(data: dict) -> None:
    cf = _license_cache_file()
    cf.write_text(json.dumps({**data, "cachedAt": time.time()}), encoding="utf-8")


def _ssl_context():
    """Return an SSL context with a valid CA bundle.

    PyInstaller binaries on macOS don't automatically inherit the system cert
    store.  Try certifi (bundled as a dep of many packages), then the macOS
    system cert file, then fall back to the default context.
    """
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    import os
    for path in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(path):
            return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


def _validate_key_online(key: str) -> dict:
    """POST to the Cloudflare Worker. Returns {valid, customer, expiry, error?}."""
    import urllib.request, urllib.error
    body = json.dumps({"key": key}).encode()
    req  = urllib.request.Request(
        f"{_LICENSE_WORKER_URL}/validate",
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "sphere-cli/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError as e:
        raise ConnectionError(str(e)) from e


def _check_license() -> None:
    """Called at the top of every gated command.

    Raises SystemExit(1) with a user-friendly message if the license is
    missing or invalid.  Skipped entirely when SPHERE_LICENSE_REQUIRED=false.
    """
    if not _LICENSE_REQUIRED:
        return

    key = _read_stored_license_key()
    if not key:
        print(
            "✗  No SPHERE license found.\n"
            "   Run:  sphere license activate <key>\n"
            "   Contact zihuai@stanford.edu to get a license.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Try online; fall back to 7-day cache for offline use.
    try:
        result = _validate_key_online(key)
        _write_license_cache(result)
    except ConnectionError:
        result = _read_license_cache()
        if result is None:
            print(
                "✗  License server unreachable and local cache has expired.\n"
                "   Connect to the internet and re-run to refresh your license.",
                file=sys.stderr,
            )
            raise SystemExit(1)

    if not result.get("valid"):
        print(
            f"✗  License invalid: {result.get('error', 'unknown error')}\n"
            "   Run:  sphere license activate <key>",
            file=sys.stderr,
        )
        raise SystemExit(1)


def cmd_license(args: argparse.Namespace) -> int:
    sub = args.license_command

    # ── activate ──────────────────────────────────────────────────────────────
    if sub == "activate":
        key = (getattr(args, "key", None) or "").strip()
        if not key:
            try:
                import getpass
                key = getpass.getpass("SPHERE license key (input hidden): ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                return 1
        if not key.startswith("sphere_"):
            print("Error: key must start with 'sphere_'", file=sys.stderr)
            return 1
        print("Validating …", end="", flush=True)
        try:
            result = _validate_key_online(key)
        except ConnectionError as e:
            print(f"\nError: could not reach license server — {e}", file=sys.stderr)
            return 1
        if not result.get("valid"):
            print(f"\r✗ Invalid key: {result.get('error', 'unknown error')}", file=sys.stderr)
            return 1
        _write_license_key(key)
        _write_license_cache(result)
        customer = result.get("customer", "")
        expiry   = result.get("expiry")
        print(f"\r✓ License activated  —  {customer}")
        if expiry:
            print(f"  Expires: {expiry}")
        return 0

    # ── status ────────────────────────────────────────────────────────────────
    if sub == "status":
        key = _read_stored_license_key()
        if not key:
            print("✗ No license key configured.")
            print("  Run:  sphere license activate <key>")
            return 0
        print("Checking …", end="", flush=True)
        offline = False
        try:
            result = _validate_key_online(key)
            _write_license_cache(result)
        except ConnectionError:
            result = _read_license_cache()
            offline = True
            if result is None:
                print("\r✗ License server unreachable and cache expired.")
                return 1
        if result.get("valid"):
            customer = result.get("customer", "")
            expiry   = result.get("expiry")
            suffix   = "  (offline — cached)" if offline else ""
            print(f"\r✓ License valid  —  {customer}{suffix}")
            if expiry:
                print(f"  Expires: {expiry}")
        else:
            print(f"\r✗ License invalid: {result.get('error', 'unknown')}")
        return 0

    # ── clear ─────────────────────────────────────────────────────────────────
    if sub == "clear":
        removed = False
        for f in (_license_key_file(), _license_cache_file()):
            if f.exists():
                f.unlink()
                removed = True
        print("✓ License cleared." if removed else "No license stored.")
        return 0

    return 0


# ── demo ──────────────────────────────────────────────────────────────────────

def _find_example_csv() -> Path:
    """Locate the bundled NHANES sample CSV.

    Works both in a PyInstaller frozen binary (sys._MEIPASS) and during
    development (relative to this file's parent package directory).
    """
    import sys
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent.parent
    p = base / "examples" / "nhanes_sample.csv"
    if not p.exists():
        raise FileNotFoundError(f"Built-in example not found at {p}")
    return p


def cmd_demo(args: argparse.Namespace) -> int:  # noqa: ARG001
    _check_license()
    import tempfile
    from ._generate import generate
    from ._evaluate import evaluate

    print("SPHERE demo — built-in NHANES dataset (4,899 rows × 18 cols, continuous + categorical)")
    print("─" * 52)

    try:
        real_path = _find_example_csv()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    synth_path = Path(tmp.name)
    tmp.close()

    try:
        # ── Generate ──────────────────────────────────────────────────────────
        print()
        print(f"Generating synthetic data from {real_path.name} …")

        def _gen_prog(frac: float, msg: str) -> None:
            print(_bar(frac, msg), end="", flush=True)

        try:
            result = generate(
                input_path=real_path, output_path=synth_path,
                k=2, mix_prob=0.75, seed=None, on_progress=_gen_prog,
            )
        except Exception as e:
            _clear_line()
            print(f"Error: {e}", file=sys.stderr)
            return 1

        _clear_line()
        elapsed = result["elapsedMs"] / 1000
        print(f"✓ {synth_path}  {result['rows']:,} rows × {result['cols']} cols"
              f"  ({elapsed:.1f} s)  seed {result['seed']}")

        # ── Evaluate (fidelity + privacy) ─────────────────────────────────────
        print()
        print(f"Evaluating {real_path.name} vs {synth_path.name} …")

        def _eval_prog(frac: float, msg: str) -> None:
            print(_bar(frac, msg), end="", flush=True)

        try:
            result = evaluate(
                real_path=real_path, synth_path=synth_path,
                n_attacks=200, n_secrets=5, n_atk_cap=1000,
                n_neighbors=1, n_aux_cols=20,
                seed=None, skip_privacy=False, on_progress=_eval_prog,
            )
        except Exception as e:
            _clear_line()
            print(f"Error: {e}", file=sys.stderr)
            return 1

        _clear_line()
        _print_eval_results(result)

    finally:
        try:
            synth_path.unlink()
            synth_path.with_suffix(".sphere.json").unlink(missing_ok=True)
        except Exception:
            pass

    print("Try it on your own data:")
    print("  sphere generate your_data.csv -o synthetic.csv")
    print("  sphere evaluate your_data.csv synthetic.csv")
    print("  sphere certify  your_data.csv synthetic.csv -o report.html")
    print()
    return 0


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sphere",
        description="SPHERE synthetic data CLI — generate, evaluate, certify.\nScores are 0–100 (higher = better).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="sphere-synth 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── generate ──────────────────────────────────────────────────────────────
    gen = sub.add_parser("generate", help="Generate a synthetic CSV from a real one using SPHERE")
    gen.add_argument("input",              type=Path,  help="Real input CSV")
    gen.add_argument("-o", "--output",     type=Path,  required=True, help="Output path for the synthetic CSV")
    gen.add_argument("--k",                type=int,   default=2,    help="Number of SPHERE rotations (default 2)")
    gen.add_argument("--theta",            type=float, default=None, help="Rotation angle in radians (default π/6 ≈ 0.524)")
    gen.add_argument("--delta",            type=float, default=None, help="Per-pair angle half-width in radians (default 5° ≈ 0.087)")
    gen.add_argument("--mix-prob",         type=float, default=0.75, dest="mix_prob",
                     help="P(use θ vs π−θ) per pair — privacy/utility trade-off (default 0.75)")
    gen.add_argument("--seed",             type=int,   default=None, help="Integer RNG seed for reproducible output")
    gen.add_argument("--json",             action="store_true",      help="Machine-readable JSON output")

    # ── evaluate ──────────────────────────────────────────────────────────────
    ev = sub.add_parser("evaluate", help="Evaluate fidelity and privacy of a real/synthetic CSV pair")
    ev.add_argument("real",                type=Path, help="Real CSV")
    ev.add_argument("synth",               type=Path, help="Synthetic CSV")
    ev.add_argument("--n-attacks",         type=int,  default=500,  help="Anonymeter attack count per metric (default 500)")
    ev.add_argument("--n-secrets",         type=int,  default=5,    help="Secret columns averaged for inference risk (default 5)")
    ev.add_argument("--n-atk-cap",         type=int,  default=2000, help="Row subsample cap for Anonymeter (default 2000)")
    ev.add_argument("--n-neighbors",       type=int,  default=1,    help="k for linkability k-NN test (default 1)")
    ev.add_argument("--n-aux-cols",        type=int,  default=20,   help="Feature columns for linkability A/B split (default 20)")
    ev.add_argument("--seed",              type=int,  default=None, help="Integer RNG seed for reproducibility")
    ev.add_argument("--skip-privacy",      action="store_true",     help="Compute fidelity only (faster)")
    ev.add_argument("--json",              action="store_true",     help="Machine-readable JSON output")

    # ── certify ───────────────────────────────────────────────────────────────
    cert = sub.add_parser(
        "certify",
        help="Evaluate a real/synthetic pair and produce an HTML certificate",
        description=(
            "Runs a full evaluation and writes a self-contained HTML certificate file.\n"
            "Open the HTML in any browser; use File → Print → Save as PDF to archive.\n\n"
            "If the synthetic was produced with sphere generate, generation parameters\n"
            "are read automatically from the .sphere.json sidecar written alongside it.\n"
            "Manual flags (--k, --theta, --seed-gen, …) override the sidecar values."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cert.add_argument("real",              type=Path,  help="Real CSV")
    cert.add_argument("synth",             type=Path,  help="Synthetic CSV")
    cert.add_argument("-o", "--output",    type=Path,  required=True, help="Output HTML certificate file")
    # Evaluation options
    cert.add_argument("--n-attacks",       type=int,   default=500)
    cert.add_argument("--n-secrets",       type=int,   default=5)
    cert.add_argument("--n-atk-cap",       type=int,   default=2000)
    cert.add_argument("--n-neighbors",     type=int,   default=1)
    cert.add_argument("--n-aux-cols",      type=int,   default=20)
    cert.add_argument("--seed",            type=int,   default=None, help="Evaluation RNG seed")
    cert.add_argument("--skip-privacy",    action="store_true",      help="Fidelity only (faster)")
    # Generation provenance (optional — marks certificate as SPHERE-generated)
    cert.add_argument("--k",              type=int,   default=None, dest="k",
                      help="sphere generate --k used to produce synth (marks certificate SPHERE-generated)")
    cert.add_argument("--theta",          type=float, default=None,
                      help="sphere generate --theta used")
    cert.add_argument("--delta",          type=float, default=None,
                      help="sphere generate --delta used")
    cert.add_argument("--mix-prob",       type=float, default=None, dest="mix_prob",
                      help="sphere generate --mix-prob used")
    cert.add_argument("--seed-gen",       type=int,   default=None, dest="seed_gen",
                      help="sphere generate --seed used")
    cert.add_argument("--generated-at",   type=str,   default=None, dest="generated_at",
                      help="ISO-8601 timestamp when sphere generate was run")
    cert.add_argument("--json",           action="store_true",      help="Machine-readable JSON output")

    # ── demo ──────────────────────────────────────────────────────────────────
    sub.add_parser("demo", help="Run generate + evaluate on the built-in NHANES dataset (continuous + categorical)")

    # ── license ───────────────────────────────────────────────────────────────
    lic_p = sub.add_parser(
        "license",
        help="Activate, check, or clear your SPHERE license",
        description=(
            "Manage the SPHERE license key.\n\n"
            "The key is validated against the SPHERE license server and cached\n"
            "locally for up to 7 days so the CLI works offline.\n\n"
            "Set SPHERE_LICENSE_REQUIRED=false to run without a license check\n"
            "(unlocked / research builds only)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    lic_sub = lic_p.add_subparsers(dest="license_command", required=True)

    lic_act = lic_sub.add_parser("activate", help="Activate with a sphere_… license key")
    lic_act.add_argument("key", nargs="?", metavar="KEY",
                         help="License key (sphere_…). Omit to be prompted.")
    lic_sub.add_parser("status", help="Show current license status")
    lic_sub.add_parser("clear",  help="Remove the stored license key and cache")

    args = parser.parse_args()
    if   args.command == "generate": sys.exit(cmd_generate(args))
    elif args.command == "evaluate": sys.exit(cmd_evaluate(args))
    elif args.command == "certify":  sys.exit(cmd_certify(args))
    elif args.command == "demo":     sys.exit(cmd_demo(args))
    elif args.command == "license":  sys.exit(cmd_license(args))


if __name__ == "__main__":
    main()
