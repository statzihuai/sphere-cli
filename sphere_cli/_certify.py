"""
Certificate HTML generation — exact Python port of buildCertificateHtml()
from src/App.tsx.  Produces byte-for-byte identical HTML to the Electron app.
"""
from __future__ import annotations

import hashlib
import html as _html
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Helpers ───────────────────────────────────────────────────────────────────

def _escape(s: str) -> str:
    return _html.escape(str(s), quote=True)

def _fmt_bytes(b: int) -> str:
    if b > 1_000_000:
        return f"{b / 1_000_000:.2f} MB"
    if b > 1_000:
        return f"{b / 1_000:.1f} KB"
    return f"{b} B"

def _fmt_pct(n: float) -> str:
    return f"{n:.2f}"

def _fmt_raw(n: float) -> str:
    return f"{n:.2e}"


def _file_stats(path: Path) -> dict:
    """Return name, path, size, mtime (ISO), sha256 for a file."""
    stat = path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "name":   path.name,
        "path":   str(path.resolve()),
        "size":   stat.st_size,
        "mtime":  mtime,
        "sha256": sha256,
    }


# ── Certificate builder ───────────────────────────────────────────────────────

def build_certificate_html(
    *,
    result: dict,
    real_path: Path | str,
    synth_path: Path | str,
    generation_params: dict | None = None,
    generated_at: str | None = None,
) -> str:
    """Produce the SPHERE Certificate HTML.

    Parameters
    ----------
    result          : dict returned by sphere_cli._evaluate.evaluate()
    real_path       : path to the real CSV (for file stats / SHA-256)
    synth_path      : path to the synthetic CSV
    generation_params : dict with keys theta, delta, mix_prob, k, seed
                        (present iff the synthetic was produced by sphere generate)
    generated_at    : ISO-8601 timestamp from sphere generate (optional)
    """
    real_path  = Path(real_path)
    synth_path = Path(synth_path)

    real_stats  = _file_stats(real_path)
    synth_stats = _file_stats(synth_path)

    issued_at         = datetime.now(tz=timezone.utc).isoformat()
    is_sphere_generated = generation_params is not None

    fid  = result["fidelity"]
    priv = result.get("privacy")
    r    = result

    def row(k: str, v: str, d: str) -> str:
        return f"<tr><th>{k}</th><td>{v}</td><td class=\"hint\">{_escape(d)}</td></tr>"

    # ── Parameter descriptions ────────────────────────────────────────────────
    PARAM_DESC = {
        "theta":       "Rotation angle centre. Default π/6 (30°). Each row pair is rotated by an angle drawn near θ.",
        "delta":       "Half-width of the per-pair angle jitter. Default 5°; each pair draws θ ∈ [θ−δ, θ+δ]. Set 0 for a fixed angle.",
        "mix_prob":    "Probability that a pair uses θ vs π−θ. Default 0.75; the π−θ flip enlarges the reconstruction orbit.",
        "k":           "Number of independent rotations stacked (SPHERE×k). More = stronger privacy at the cost of some ML utility.",
        "seed":        "Integer seed for reproducible generation. Empty = a fresh random draw was used.",
        "nAttacks":    "Monte-Carlo attack draws per metric. Higher = tighter confidence interval, slower run.",
        "nAuxCols":    "Number of feature columns drawn for the Linkability A/B split (split half/half). Anonymeter convention: 20.",
        "nNeighbors":  "k for the Linkability k-NN intersect test. Anonymeter default 1; larger k = more permissive linking.",
        "nSecrets":    "Number of secret-column choices averaged for the Inference attack. Higher = more stable score, slower run.",
        "nAtkCap":     "Subsample cap on the attacker input rows passed to Anonymeter. Anonymeter convention: min(2000, n).",
        "evalSeed":    "Integer seed for reproducible evaluation. (random) = fresh Monte-Carlo realisation each run.",
    }

    # ── Generation method rows ────────────────────────────────────────────────
    if is_sphere_generated:
        gp = generation_params
        seed_val = str(gp["seed"]) if gp.get("seed") is not None else "<em>random</em>"
        method_row = row("method", "<strong>SPHERE</strong> (CLI)",
                         "The synthetic CSV was generated via sphere generate; the parameters below were captured at generation time.")
        gen_rows = (
            method_row
            + row("seed",         seed_val,                    PARAM_DESC["seed"])
            + row("generated at", generated_at or "—",         "Wall-clock timestamp when SPHERE was applied.")
            + row("k",            str(gp.get("k",  2)),        PARAM_DESC["k"])
            + row("theta",        f"{gp.get('theta', 0):.4f}", PARAM_DESC["theta"])
            + row("delta",        f"{gp.get('delta', 0):.4f}", PARAM_DESC["delta"])
            + row("mix_prob",     str(gp.get("mix_prob", 0.75)), PARAM_DESC["mix_prob"])
        )
    else:
        method_row = row("method", "<strong>External</strong> (user-supplied)",
                         "The synthetic CSV was supplied directly. The app cannot verify which method produced it; "
                         "the evaluation scores below are independent of generation method and remain valid.")
        gen_rows = method_row

    # ── Evaluation parameter rows ─────────────────────────────────────────────
    params = r.get("params")
    if params:
        seed_str = f"{params.get('seed', '—')}"
        eval_rows = (
            row("n_attacks",              str(params.get("nAttacks",   "—")), PARAM_DESC["nAttacks"])
            + row("n_aux_cols (Linkability)",  str(params.get("nAuxCols",   "—")), PARAM_DESC["nAuxCols"])
            + row("n_neighbors (Linkability)", str(params.get("nNeighbors", "—")), PARAM_DESC["nNeighbors"])
            + row("n_secrets (Inference)",     str(params.get("nSecrets",   "—")), PARAM_DESC["nSecrets"])
            + row("n_atk_cap",                 str(params.get("nAtkCap",    "—")), PARAM_DESC["nAtkCap"])
            + row("seed",                      seed_str,                            PARAM_DESC["evalSeed"])
        )
    else:
        eval_rows = '<tr><td colspan="3" class="muted">Evaluator parameters not recorded.</td></tr>'

    # ── Blurbs and descriptions ───────────────────────────────────────────────
    FIDELITY_BLURB = (
        "How statistically close is the synthetic to the real data? All four metrics are computed on the "
        "encoded matrix (categoricals → ±1 indicator columns). Higher = better; 100 = identical."
    )
    PRIVACY_BLURB = (
        "How resistant is the synthetic to three GDPR-aligned re-identification attacks (Anonymeter). "
        "Each score normalises the raw attack rate against two anchors: real-vs-real (max-risk baseline) "
        "and real-vs-column-shuffled (min-risk baseline). Higher = more private; "
        "100 = no better than column-shuffle."
    )
    FID_DESC = {
        "mean": "Max column-mean deviation. SPHERE preserves column means exactly → score should be ~100.",
        "var":  "Max column-variance deviation. SPHERE preserves column variances exactly → score should be ~100.",
        "cor":  "Frobenius distance between the two correlation matrices. Preserved exactly for fully numeric data; "
                "some drift when categoricals are present (see note below).",
        "ks":   "Mean column-wise Kolmogorov–Smirnov statistic. Catches per-column distribution shifts "
                "that means and variances miss.",
    }
    PRIV_DESC = {
        "so":  "Can an attacker isolate a single individual using a query built from the synthetic data? "
               "The attacker picks 1–3 attribute values from a synthetic row and counts unique matches in real.",
        "lk":  "If an attacker knows half of an individual's attributes (set A), can the synthetic data tell "
               "them the other half (set B)? k-NN-based linking across a random column split.",
        "inf": "Can an attacker who knows all of an individual's attributes except one ('the secret') predict "
               "that secret using the synthetic data as training? Averaged over multiple secret-column choices.",
    }

    corr_note = ""
    if r.get("categoricalCols", 0) > 0:
        corr_note = (
            '<div class="callout"><strong>Note on correlation with categorical variables.</strong> '
            "SPHERE preserves correlations exactly for fully numeric data. When categorical columns are "
            "present, small correlation drift may occur due to encoding and decoding of category labels. "
            "<strong>For exact correlation preservation</strong>, pre-encode categoricals as ±1 indicator "
            "columns in the source CSV (one column per category, +1 = belongs, −1 = does not) and treat "
            "them as numeric.</div>"
        )

    # ── Privacy section ───────────────────────────────────────────────────────
    p_orig     = r.get("pOrig", r.get("pEnc", "—"))
    num_cols   = r.get("numericCols", "—")
    cat_cols   = r.get("categoricalCols", "—")
    engine     = r.get("engine", "sphere-cli")
    elapsed_ms = r.get("elapsedMs", 0)

    if priv:
        so  = priv["singlingOut"]
        lk  = priv["linkability"]
        inf = priv["inference"]
        privacy_section = f"""
  <section>
    <h2>Privacy</h2>
    <p class="blurb">{_escape(PRIVACY_BLURB)}</p>
    <div class="composite">
      <span>Composite score</span><span class="v">{_fmt_pct(priv["composite"])} / 100</span>
    </div>
    <div class="scores three">
      <div class="score"><div class="label">Singling-out</div><div class="val">{_fmt_pct(so["score"])}</div><div class="sub">real {so["rReal"]:.3f} · shuf {so["rShuffle"]:.3f} · synth {so["rSynth"]:.3f}</div><div class="desc">{_escape(PRIV_DESC["so"])}</div></div>
      <div class="score"><div class="label">Linkability</div><div class="val">{_fmt_pct(lk["score"])}</div><div class="sub">real {lk["rReal"]:.3f} · shuf {lk["rShuffle"]:.3f} · synth {lk["rSynth"]:.3f}</div><div class="desc">{_escape(PRIV_DESC["lk"])}</div></div>
      <div class="score"><div class="label">Inference</div><div class="val">{_fmt_pct(inf["score"])}</div><div class="sub">real {inf["rReal"]:.3f} · shuf {inf["rShuffle"]:.3f} · synth {inf["rSynth"]:.3f}</div><div class="desc">{_escape(PRIV_DESC["inf"])}</div></div>
    </div>
  </section>"""
    else:
        privacy_section = """
  <section>
    <h2>Privacy</h2>
    <p class="blurb muted">Privacy evaluation was skipped (--skip-privacy).</p>
  </section>"""

    seal = "SPHERE-generated" if is_sphere_generated else "evaluation-only"

    # ── Full HTML document ────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>SPHERE Certificate — {_escape(synth_stats["name"])}</title>
<style>
  :root {{
    --bg: #faf7f2; --surface: #fff; --surface-2: #f8f1e2;
    --border: #e6dfd0; --text: #1a1817; --muted: #7a7468;
    --cardinal: #8C1515; --sage: #3f4a2e; --gold: #a87d3a;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px;
    font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'SF Pro Text', 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text); font-size: 13px; line-height: 1.55;
  }}
  .doc {{ max-width: 880px; margin: 0 auto; }}
  header {{ display: flex; align-items: center; gap: 18px; padding-bottom: 16px; border-bottom: 2px solid var(--cardinal); }}
  header svg {{ width: 56px; height: 56px; flex-shrink: 0; }}
  header h1 {{ margin: 0; font-size: 22px; font-weight: 600; letter-spacing: -0.01em; }}
  header .sub {{ color: var(--muted); font-size: 12px; margin-top: 2px; }}
  section {{ margin-top: 22px; padding: 16px 20px; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }}
  section h2 {{ margin: 0 0 10px 0; font-size: 14px; font-weight: 600; letter-spacing: 0.02em; color: var(--cardinal); text-transform: uppercase; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; table-layout: fixed; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  th {{ width: 200px; color: var(--muted); font-weight: 500; }}
  td {{ color: var(--text); font-family: 'SF Mono', Menlo, monospace; word-break: break-all; }}
  td.hint {{ color: var(--muted); font-family: -apple-system, sans-serif; font-size: 11px; line-height: 1.45; word-break: normal; }}
  .muted {{ color: var(--muted); font-style: italic; font-family: inherit; }}
  .blurb {{ margin: 0 0 12px 0; font-size: 12px; color: var(--muted); line-height: 1.55; }}
  .scores {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 8px; }}
  .scores.three {{ grid-template-columns: repeat(3, 1fr); }}
  .score {{ background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }}
  .score .label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); font-weight: 600; }}
  .score .val {{ font-size: 22px; font-weight: 600; margin-top: 2px; font-variant-numeric: tabular-nums; }}
  .score .sub {{ font-size: 10px; color: var(--muted); font-family: 'SF Mono', Menlo, monospace; margin-top: 3px; }}
  .score .desc {{ font-size: 11px; color: var(--muted); margin-top: 6px; line-height: 1.45; }}
  .composite {{ display: flex; justify-content: space-between; align-items: baseline; padding-top: 8px; }}
  .composite .v {{ font-size: 20px; font-weight: 600; color: var(--cardinal); font-variant-numeric: tabular-nums; }}
  .callout {{ margin-top: 12px; background: rgba(168,125,58,0.10); border-left: 3px solid var(--gold); border-radius: 4px; padding: 10px 14px; font-size: 11px; line-height: 1.55; color: var(--text); }}
  .callout strong {{ color: var(--gold); font-weight: 600; }}
  .callout em {{ color: var(--sage); font-style: italic; }}
  footer {{ margin-top: 24px; padding-top: 12px; border-top: 1px solid var(--border); color: var(--muted); font-size: 11px; }}
  .seal {{ display: inline-block; padding: 4px 10px; border: 1px solid var(--sage); color: var(--sage); border-radius: 4px; font-weight: 600; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; }}
  @media print {{ body {{ background: white; padding: 0; }} section {{ break-inside: avoid; }} }}
</style>
</head>
<body>
<div class="doc">
  <header>
    <svg viewBox="0 0 120 120" aria-hidden="true">
      <circle cx="60" cy="60" r="52" fill="none" stroke="#b8945a" stroke-width="1.2" stroke-dasharray="2 4"/>
      <circle cx="48" cy="60" r="26" fill="none" stroke="#8C1515" stroke-width="2.8"/>
      <circle cx="72" cy="60" r="26" fill="none" stroke="#3f4a2e" stroke-width="2.8"/>
      <circle cx="48" cy="54" r="1.8" fill="#8C1515"/>
      <circle cx="72" cy="54" r="1.8" fill="#3f4a2e"/>
    </svg>
    <div>
      <h1>SPHERE Synthetic Data Certificate</h1>
      <div class="sub">Issued {_escape(issued_at)} · <span class="seal">{seal}</span></div>
    </div>
  </header>

  <section>
    <h2>Source data (real)</h2>
    <p class="blurb">The original dataset that was used as input to SPHERE. The SHA-256 hash uniquely identifies the exact bytes evaluated.</p>
    <table>
      {row("file",     _escape(real_stats["name"]),   "Source CSV file name.")}
      {row("path",     _escape(real_stats["path"]),   "Absolute filesystem path at evaluation time.")}
      {row("size",     _fmt_bytes(real_stats["size"]), "Disk size of the CSV.")}
      {row("modified", real_stats["mtime"],            "Filesystem mtime — when the file was last written.")}
      {row("SHA-256",  real_stats["sha256"],           "Tamper-evident fingerprint. Re-hashing the same file yields the same digest.")}
      {row("rows",     str(r["nReal"]),                "Number of records in the source data.")}
      {row("columns",  f"{p_orig} ({num_cols} numeric, {cat_cols} categorical)", "Original column count and type breakdown. Categoricals are expanded to ±1 indicator columns for fidelity scoring.")}
    </table>
  </section>

  <section>
    <h2>Synthetic data</h2>
    <p class="blurb">The synthetic dataset produced by SPHERE (or supplied by the user). Same fingerprint convention as the source.</p>
    <table>
      {row("file",     _escape(synth_stats["name"]),   "Synthetic CSV file name.")}
      {row("path",     _escape(synth_stats["path"]),   "Absolute filesystem path at evaluation time.")}
      {row("size",     _fmt_bytes(synth_stats["size"]), "Disk size of the CSV.")}
      {row("modified", synth_stats["mtime"],            "Filesystem mtime — when the file was last written.")}
      {row("SHA-256",  synth_stats["sha256"],           "Tamper-evident fingerprint of the synthetic file.")}
      {row("rows",     str(r["nSynth"]),                "Number of records in the synthetic data.")}
    </table>
  </section>

  <section>
    <h2>Generation method</h2>
    <p class="blurb">{"The synthetic CSV was generated by SPHERE (sphere generate). The parameters below were captured at generation time." if is_sphere_generated else "The synthetic CSV was supplied directly. The certificate stamps the file fingerprint (SHA-256 above) and the evaluation scores below; it does not certify how the synthetic was produced."}</p>
    <table>{gen_rows}</table>
  </section>

  <section>
    <h2>Fidelity</h2>
    <p class="blurb">{_escape(FIDELITY_BLURB)}</p>
    <div class="composite">
      <span>Composite score</span><span class="v">{_fmt_pct(fid["composite"])} / 100</span>
    </div>
    <div class="scores">
      <div class="score"><div class="label">Δ mean</div><div class="val">{_fmt_pct(fid["meanScore"])}</div><div class="sub">raw Δ = {_fmt_raw(fid["pctDeltaMean"])}%</div><div class="desc">{_escape(FID_DESC["mean"])}</div></div>
      <div class="score"><div class="label">Δ variance</div><div class="val">{_fmt_pct(fid["varScore"])}</div><div class="sub">raw Δ = {_fmt_raw(fid["pctDeltaVar"])}%</div><div class="desc">{_escape(FID_DESC["var"])}</div></div>
      <div class="score"><div class="label">Δ correlation</div><div class="val">{_fmt_pct(fid["corScore"])}</div><div class="sub">raw Δ = {_fmt_raw(fid["pctDeltaCor"])}%</div><div class="desc">{_escape(FID_DESC["cor"])}</div></div>
      <div class="score"><div class="label">Marginals (KS)</div><div class="val">{_fmt_pct(fid["ksScore"])}</div><div class="sub">KS = {fid["ksStatistic"]:.3f}</div><div class="desc">{_escape(FID_DESC["ks"])}</div></div>
    </div>
    {corr_note}
  </section>
{privacy_section}

  <section>
    <h2>Evaluation parameters</h2>
    <p class="blurb">Parameters supplied to the evaluator. The engine name identifies the underlying implementation; <code>seed</code> determines the Monte-Carlo realisation.</p>
    <table>
      {row("engine",  _escape(engine),                          "Underlying evaluator. anonymeter-v1.0.1 = bundled Python; sphere-cli = CLI binary.")}
      {row("elapsed", f"{elapsed_ms / 1000:.2f} s",             "Wall-clock time for the full evaluation (3 metrics × 3 baselines).")}
      {eval_rows}
    </table>
  </section>

  <footer>
    Each privacy score normalizes the attack rate against two anchors:
    real-vs-real (max-leak) and real-vs-column-shuffled (min-leak), giving
    a 0–100 score where 100 = no better than column-shuffle.
    Fidelity scores are 100 minus the relative deviation, computed on the
    ±1-encoded matrix (categoricals → indicator columns).
    The SHA-256 hashes above identify the exact CSV bytes evaluated;
    re-running on the same files with the same evaluator parameters and
    seed reproduces the scores within Monte-Carlo noise.
  </footer>
</div>
</body>
</html>"""
