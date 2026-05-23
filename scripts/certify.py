#!/usr/bin/env python3
"""Thin subprocess wrapper — evaluate and produce an HTML certificate.

Called by sphere.js as:
  python3 <this-file> real.csv synth.csv -o cert.html [options]

Streams {"progress": f, "msg": "..."} JSON lines to stderr.
Writes {"ok": true, "output": "cert.html", "elapsedMs": N} to stdout on success,
or {"error": "..."} on failure.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('real')
    p.add_argument('synth')
    p.add_argument('-o', '--output', required=True)
    p.add_argument('--skip-privacy',  action='store_true')
    p.add_argument('--n-attacks',     type=int,   default=500)
    p.add_argument('--n-secrets',     type=int,   default=5)
    p.add_argument('--n-atk-cap',     type=int,   default=2000)
    p.add_argument('--n-neighbors',   type=int,   default=1)
    p.add_argument('--n-aux-cols',    type=int,   default=20)
    p.add_argument('--seed',          type=int,   default=None)
    # Generation provenance (optional — marks certificate as SPHERE-generated)
    p.add_argument('--k',             type=int,   default=None)
    p.add_argument('--theta',         type=float, default=None)
    p.add_argument('--delta',         type=float, default=None)
    p.add_argument('--mix-prob',      type=float, default=None)
    p.add_argument('--seed-gen',      type=int,   default=None)
    p.add_argument('--generated-at',  type=str,   default=None)
    args = p.parse_args()

    def on_progress(frac: float, msg: str) -> None:
        sys.stderr.write(json.dumps({'progress': round(frac, 4), 'msg': msg}) + '\n')
        sys.stderr.flush()

    try:
        from sphere_cli._evaluate import evaluate   # type: ignore
        from sphere_cli._certify  import build_certificate_html  # type: ignore
    except ImportError as e:
        sys.stderr.write(json.dumps({'error': f'Evaluation engine not available: {e}'}) + '\n')
        sys.exit(1)

    t0 = time.perf_counter()

    try:
        result = evaluate(
            real_path    = Path(args.real),
            synth_path   = Path(args.synth),
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
        sys.stderr.write(json.dumps({'error': str(e)}) + '\n')
        sys.exit(1)
    except Exception as e:
        import traceback
        sys.stderr.write(json.dumps({'error': f'Unexpected error: {e}\n{traceback.format_exc()}'}) + '\n')
        sys.exit(1)

    result['elapsedMs'] = int((time.perf_counter() - t0) * 1000)

    # Auto-load .sphere.json sidecar written by sphere generate
    synth_path = Path(args.synth)
    meta: dict = {}
    meta_path = synth_path.with_suffix('.sphere.json')
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
        except Exception:
            pass

    # CLI flags win; sidecar fills gaps; defaults last
    _theta    = args.theta    if args.theta    is not None else meta.get('theta')
    _delta    = args.delta    if args.delta    is not None else meta.get('delta')
    _mix_prob = args.mix_prob if args.mix_prob is not None else meta.get('mix_prob')
    _k        = args.k        if args.k        is not None else meta.get('k')
    _seed_gen = args.seed_gen if args.seed_gen is not None else meta.get('seed')
    _gen_at   = args.generated_at if args.generated_at is not None else meta.get('generated_at')

    import math
    gen_params = None
    if any(x is not None for x in [_theta, _delta, _mix_prob, _k, _seed_gen, _gen_at]):
        gen_params = {
            'theta':    _theta    if _theta    is not None else math.pi / 6,
            'delta':    _delta    if _delta    is not None else 5 * math.pi / 180,
            'mix_prob': _mix_prob if _mix_prob is not None else 0.75,
            'k':        _k        if _k        is not None else 2,
            'seed':     _seed_gen,
        }

    try:
        html = build_certificate_html(
            result            = result,
            real_path         = Path(args.real),
            synth_path        = synth_path,
            generation_params = gen_params,
            generated_at      = _gen_at,
        )
    except Exception as e:
        sys.stderr.write(json.dumps({'error': f'Certificate build failed: {e}'}) + '\n')
        sys.exit(1)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding='utf-8')

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    sys.stdout.write(json.dumps({'ok': True, 'output': str(out_path), 'elapsedMs': elapsed_ms}) + '\n')
    sys.exit(0)


if __name__ == '__main__':
    main()
