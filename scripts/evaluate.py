#!/usr/bin/env python3
"""Thin subprocess wrapper — evaluate a real/synthetic CSV pair.

Called by sphere.js as:
  python3 <this-file> real.csv synth.csv [options]

Streams {"progress": f, "msg": "..."} JSON lines to stderr.
Writes the final result dict as a single JSON line to stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make sphere_cli importable from the npm package root (one level up)
_PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('real')
    p.add_argument('synth')
    p.add_argument('--skip-privacy', action='store_true')
    p.add_argument('--n-attacks',   type=int,   default=500)
    p.add_argument('--n-secrets',   type=int,   default=5)
    p.add_argument('--n-atk-cap',   type=int,   default=2000)
    p.add_argument('--n-neighbors', type=int,   default=1)
    p.add_argument('--n-aux-cols',  type=int,   default=20)
    p.add_argument('--seed',        type=int,   default=None)
    args = p.parse_args()

    def on_progress(frac: float, msg: str) -> None:
        sys.stderr.write(json.dumps({'progress': round(frac, 4), 'msg': msg}) + '\n')
        sys.stderr.flush()

    try:
        from sphere_cli._evaluate import evaluate  # type: ignore
    except ImportError as e:
        sys.stderr.write(json.dumps({'error': f'Evaluation engine not available: {e}'}) + '\n')
        sys.exit(1)

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
        sys.stdout.write(json.dumps(result) + '\n')
        sys.exit(0)
    except ValueError as e:
        sys.stderr.write(json.dumps({'error': str(e)}) + '\n')
        sys.exit(1)
    except Exception as e:
        import traceback
        sys.stderr.write(json.dumps({'error': f'Unexpected error: {e}\n{traceback.format_exc()}'}) + '\n')
        sys.exit(1)


if __name__ == '__main__':
    main()
