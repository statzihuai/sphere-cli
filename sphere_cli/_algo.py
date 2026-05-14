"""
SPHERE — Synthetic Privacy via Hypersphere Rotation of Entries.

Unified angle scheme
---------------------
Every row pair r draws an independent angle
    θ_r ~ Uniform[theta - delta, theta + delta]    (delta=5° by default)
and independently selects θ_r or π − θ_r with probability
(mix_prob, 1 − mix_prob).  This unified scheme applies to ALL
column types — continuous, ±1-coded binary/categorical, and the
±1 indicator columns produced by encode-decode — so the
reconstruction orbit is uncountably infinite regardless of variable
type, with no auto-detection of column types required.

mix_prob applies to every pair regardless of delta value (including
delta=0).  Set force_mixed_angle=False to disable the π−θ variant
entirely (all sign multipliers = +1).

Two modes for handling nominal categorical data:

  * "orthogonal" (default for pre-coded data):
      All columns are rotated with the same per-pair Givens matrix.
      Binary/categorical columns must be pre-coded as ±1 contrasts.
      Categorical output is a continuous relaxation.

      Guarantees:
        - Z*'Z* = Z'Z exactly (every realisation)
        - OLS sufficient statistics {Z'Z, Z'y} preserved exactly
        - Reconstruction orbit: uncountably infinite (delta > 0, else n−1)

  * "encode_decode" (discrete output with exact marginal preservation):
      Each K-class categorical column is expanded into K ±1 indicator
      columns, SPHERE is applied to the full expanded matrix (same
      unified angle scheme), and scores are decoded back to discrete
      labels with EXACT original count preservation.

      Decode procedure (per categorical column):
        1. Confident rows  — exactly one X*_k > 0: assign directly.
        2. Uncertain rows  — conflict or unassigned: competitive
           priority queue enforces exact counts.
        3. Guarantee: pool_size = total_budget (Σ δ_k = 0).

      Guarantees:
        - Z*'Z* = Z'Z exactly (full expanded matrix)
        - #{X* = k} = #{X = k} for all k (exact marginal preservation)
        - Cross-products between all columns preserved EXACTLY

Choose "orthogonal" when downstream analysis depends only on Z'Z and
continuous relaxation of categoricals is acceptable.
Choose "encode_decode" when discrete category labels are required with
exact marginal preservation (tree-based models, display, mixed analyses).
"""
import numpy as np

# ── Optional Numba acceleration ───────────────────────────────────────────────
# If numba is available (it is bundled in the sidecar via sphere-eval.spec),
# use JIT-compiled parallel kernels for the two innermost hot paths in sphere():
#
#   _givens_inplace_nb  — replaces _givens_orthogonal's two large row copies
#                         + broadcast temporaries (saves ~2 GB alloc per call)
#   _householder_nb     — replaces the (v[:,None] * Zt[pi]).sum(0) pattern
#                         that creates a (k×p) intermediate (~520 MB per call)
#
# Both kernels write Zt in-place and parallelize with prange, which is safe
# because:
#   • Givens   — prange over pairs r: rows pi[r] and pj[r] are unique to
#                each pair (permutation → no overlap), so no data races.
#   • Householder prange over columns c: different columns are independent.
#
# With k=10 sphere() calls on a 44k × 2924 matrix the total allocation
# savings are ~30 GB; combined with multi-core parallelism this typically
# gives a 3-6× wall-clock speedup over the pure-NumPy path.
#
# Falls back silently to NumPy when numba is absent (dev / unit-test envs).

# Numba JIT is disabled for the CLI build: Cython compilation already provides
# native machine-code performance and code protection.  The Numba-accelerated
# kernels (_givens_inplace_nb, _householder_*_nb) and the entire conditional
# block are removed here; all hot paths fall through to the NumPy
# implementations below.
_NUMBA = False


def _givens_orthogonal(Zt, pi, pj, c, s, signs):
    """Apply per-pair Givens rotation with sign ∈ {+1, -1} on the cos term.

    signs[r] = +1 → angle θ_r;   signs[r] = -1 → angle π − θ_r.

    c, s may be scalars (fixed θ, delta=0) or length-k arrays (per-pair
    random θ, delta>0).  Fast scalar path when all signs are +1 and c is
    a scalar.
    """
    # ── NumPy implementation ──────────────────────────────────────────────────
    ri, rj = Zt[pi].copy(), Zt[pj].copy()
    if np.ndim(c) == 0:
        # Scalar θ (delta=0) — existing fast paths.
        if signs is None or np.all(signs == 1.0):
            Zt[pi] = c * ri - s * rj
            Zt[pj] = s * ri + c * rj
        else:
            sc = signs[:, None] * c
            Zt[pi] = sc * ri - s * rj
            Zt[pj] = s * ri + sc * rj
    else:
        # Per-pair θ_r arrays (delta>0).
        sc  = (signs * c)[:, None]   # (k, 1)
        s_b = s[:, None]              # (k, 1)
        Zt[pi] = sc * ri - s_b * rj
        Zt[pj] = s_b * ri + sc * rj
    return Zt


def _householder_orthogonal(Zt, pi, pj, idx, n, c, s, signs, n_threshold):
    """Householder correction for orthogonal mode.

    Image of 1 under the Givens map for pair r:
      w_i[r] = signs[r]·c_r − s_r
      w_j[r] = signs[r]·c_r + s_r
    So v = w − 1:
      v_i[r] = signs[r]·c_r − s_r − 1
      v_j[r] = signs[r]·c_r + s_r − 1

    c, s may be scalars (fixed θ) or length-k arrays (per-pair random θ).
    Fast path to _householder_fixed when c is scalar and all signs are +1.
    """
    if np.ndim(c) == 0 and (signs is None or np.all(signs == 1.0)):
        # Fixed θ, no sign flips — only 2 distinct v values.
        return _householder_fixed(Zt, pi, pj, idx, n, c, s, n_threshold)

    v_i = signs * c - s - 1.0
    v_j = signs * c + s - 1.0
    norm2 = float((v_i * v_i).sum() + (v_j * v_j).sum())
    if norm2 <= 1e-24:
        return Zt

    if n < n_threshold:
        # Dense: build full n-vector v, then one outer-product update.
        v = np.zeros(n, dtype=Zt.dtype)
        v[pi] = v_i
        v[pj] = v_j
        if n % 2 == 1:
            v[idx[-1]] = 0.0
        proj = v @ Zt
        Zt -= (2.0 / norm2) * np.outer(v, proj)
    else:
        # Sparse NumPy: per-pair projection and broadcast update.
        proj = (v_i[:, None] * Zt[pi]).sum(0) + (v_j[:, None] * Zt[pj]).sum(0)
        f = 2.0 / norm2
        Zt[pi] -= (f * v_i[:, None]) * proj[None, :]
        Zt[pj] -= (f * v_j[:, None]) * proj[None, :]
    return Zt


def _householder_fixed(Zt, pi, pj, idx, n, c, s, n_threshold):
    """Householder correction for fixed-θ rotation (original SORSD).

    Image of 1:  v_i = (c − s) − 1,  v_j = (c + s) − 1   (only two values).
    """
    v_i = (c - s) - 1.0
    v_j = (c + s) - 1.0
    k = n // 2
    norm2 = k * (v_i ** 2 + v_j ** 2)
    if norm2 <= 1e-24:
        return Zt

    if n < n_threshold:
        v = np.zeros(n, dtype=Zt.dtype)
        v[pi] = v_i
        v[pj] = v_j
        if n % 2 == 1:
            v[idx[-1]] = 0.0
        proj = v @ Zt
        Zt -= (2.0 / norm2) * np.outer(v, proj)
    else:
        proj = v_i * Zt[pi].sum(0) + v_j * Zt[pj].sum(0)
        f = 2.0 / norm2
        Zt[pi] -= (f * v_i) * proj
        Zt[pj] -= (f * v_j) * proj
    return Zt


def _has_orthogonal_categorical(Z) -> bool:
    """Return True iff any column of Z contains only the values {-1, +1}.

    Vectorised: O(n*p) total — one matrix-wide |Z|==1 reduction plus a
    cheap per-candidate-column check. Avoids np.unique on every column.
    """
    # Fast path: a column has only ±1 iff |z| == 1 for every entry.
    abs_eq_one = np.all(np.abs(Z) == 1.0, axis=0)  # shape (p,)
    if not abs_eq_one.any():
        return False
    # Among ±1-only columns, require both signs present (binary, not constant).
    for j in np.flatnonzero(abs_eq_one):
        col = Z[:, j]
        if (col[0] == 1.0 and (col == -1.0).any()) or \
           (col[0] == -1.0 and (col == 1.0).any()):
            return True
    return False


# ── encode-decode helpers ─────────────────────────────────────────────────────

def _detect_string_cols(Z):
    """Return sorted list of column indices whose values cannot be cast to float.

    Fast-path: if Z has a numeric dtype, returns [] immediately.
    Otherwise, attempts ``col.astype(float)`` for each column and collects
    those that raise ValueError or TypeError.
    """
    if Z.dtype.kind in ('f', 'i', 'u'):
        return []
    cols = []
    for j in range(Z.shape[1]):
        try:
            Z[:, j].astype(float)
        except (ValueError, TypeError):
            cols.append(j)
    return cols


def _encode_categoricals(Z, cat_col_indices):
    """Expand K-class categorical columns into K ±1 indicator columns.

    Each categorical column j with categories [c_0, …, c_{K-1}] is replaced
    by K columns where indicator_k(i) = +1 if Z[i,j] == c_k else -1.

    Returns
    -------
    Z_enc    : (n, p_enc) float array — expanded matrix
    enc_info : list of dicts, one per original column:
        continuous → {'type':'cont', 'enc_col': int}
        categorical → {'type':'cat',  'orig': int, 'categories': array,
                        'counts': array, 'K': int, 'enc_cols': slice}
    """
    n, p = Z.shape
    cat_set = set(cat_col_indices)
    new_cols, enc_info, ptr = [], [], 0

    for j in range(p):
        if j not in cat_set:
            new_cols.append(Z[:, j].astype(float))   # safe for mixed-type arrays
            enc_info.append({'type': 'cont', 'orig': j, 'enc_col': ptr})
            ptr += 1
        else:
            col = Z[:, j]
            # Normalise to a uniform-dtype string array so np.sort/np.unique
            # never see a mix of str and float (NaN) — which raises TypeError
            # on Python 3.  Missing values (None, float NaN) become '' so they
            # form their own category and survive round-trip through decode.
            if col.dtype.kind == 'O':
                col = np.array(
                    ['' if (v is None or (isinstance(v, float) and v != v))
                     else str(v)
                     for v in col],
                    dtype=str,
                )
            categories = np.sort(np.unique(col))
            K = len(categories)
            counts = np.array([int((col == c).sum()) for c in categories])
            start = ptr
            for c in categories:
                new_cols.append(np.where(col == c, 1.0, -1.0))
                ptr += 1
            enc_info.append({'type': 'cat', 'orig': j,
                             'categories': categories, 'counts': counts,
                             'K': K, 'enc_cols': slice(start, ptr)})

    return np.column_stack(new_cols), enc_info


def _decode_categorical(scores, counts):
    """Decode K continuous SPHERE scores to exact-count categorical labels.

    Pipeline
    --------
    1. X*_k > 0  →  confident rows (exactly one positive): assign directly.
       SPHERE mean preservation guarantees #{X*_k > 0} ≈ n_k, so zero is
       the natural threshold.
    2. Uncertain rows (conflict: >1 positive; unassigned: 0 positive):
       initial guess via argmax, released first into the fix-up pool.
    3. Exact counts enforced via competitive priority queue on pool rows.
       Guarantee: pool_size == total_budget (by Σ δ_k = 0), so every
       pool row is assigned — #{label == k} == counts[k] for all k.

    Parameters
    ----------
    scores : (n, K) float array  — X*_k columns from SPHERE
    counts : (K,) int array      — target count per category

    Returns
    -------
    labels : (n,) int array, values in 0 .. K-1
    """
    import heapq
    n, K   = scores.shape
    counts = np.asarray(counts, dtype=int)
    assert counts.sum() == n, "counts must sum to n"

    if K == 1:
        return np.zeros(n, dtype=int)

    # ── step 1: X*_k > 0 initial assignment ─────────────────────────────────
    positive  = scores > 0                           # (n, K)
    n_pos     = positive.sum(axis=1)
    confident = (n_pos == 1)                         # exactly one positive

    assignment = np.argmax(scores, axis=1)           # fallback for uncertain

    c     = np.bincount(assignment, minlength=K)
    delta = c - counts                               # signed discrepancy

    if np.all(delta == 0):
        return assignment

    # ── step 2: partition rows ───────────────────────────────────────────────
    sorted_sc = np.sort(scores, axis=1)              # ascending, (n, K)
    margin    = sorted_sc[:, -1] - sorted_sc[:, -2]  # winner − runner-up

    pool   = []
    budget = np.zeros(K, dtype=int)

    for k in range(K):
        rows_k = np.where(assignment == k)[0]
        if delta[k] <= 0:
            budget[k] = int(-delta[k])               # empty slots to fill
        else:
            # Release delta[k] least-trusted rows: uncertain first,
            # then lowest-margin confident rows.
            unc = rows_k[~confident[rows_k]]
            con = rows_k[ confident[rows_k]]
            unc = unc[np.argsort(margin[unc])]
            con = con[np.argsort(margin[con])]
            release = np.concatenate([unc, con])[:delta[k]]
            pool.extend(release.tolist())
            budget[k] = 0

    # ── step 3: competitive priority queue ───────────────────────────────────
    # |pool| == Σ max(δ_k,0) == total_budget  → every pool row gets a slot
    heap = []
    for i in pool:
        cur_k = int(assignment[i])
        for k in range(K):
            if budget[k] > 0:
                rival = float(sorted_sc[i, -1]
                              if cur_k != k else sorted_sc[i, -2])
                heapq.heappush(heap, (-(scores[i, k] - rival), i, k))

    done = set()
    while heap:
        _, i, k = heapq.heappop(heap)
        if i in done or budget[k] == 0:
            continue
        assignment[i] = k
        done.add(i)
        budget[k] -= 1

    return assignment


def _decode_categoricals(Z_enc_star, enc_info):
    """Decode expanded SPHERE output back to original p-column layout.

    Categorical columns are decoded with exact count preservation via
    _decode_categorical(). Continuous columns are passed through unchanged.

    Returns
    -------
    Zstar : (n, p) float array
    """
    result = []
    has_object = False
    for info in enc_info:
        if info['type'] == 'cont':
            result.append(Z_enc_star[:, info['enc_col']])
        else:
            scores = Z_enc_star[:, info['enc_cols']]      # (n, K)
            labels = _decode_categorical(scores, info['counts'])
            decoded = info['categories'][labels]
            # Preserve string dtype; convert to float only for numeric categories
            try:
                result.append(decoded.astype(float))
            except (ValueError, TypeError):
                result.append(decoded)
                has_object = True

    if has_object:
        # Build object array column-by-column when any categorical column is string
        n = result[0].shape[0]
        out = np.empty((n, len(result)), dtype=object)
        for j, col in enumerate(result):
            out[:, j] = col
        return out
    return np.column_stack(result)


def sphere(Z, theta=np.pi / 6, delta=5.0 * np.pi / 180.0, mix_prob=0.75,
           categorical_cols=None, n_threshold=400, return_key=False,
           force_mixed_angle=None):
    """Generate synthetic data Z* from Z.

    Parameters
    ----------
    Z : (n, p) array
        Real data matrix.  May contain string/object columns for nominal
        categorical variables; these are handled automatically.
    theta : float in (0, π/2), default π/6
        Centre of the rotation angle interval.
    delta : float ≥ 0, default 5° (5*π/180)
        Half-width of the per-pair angle interval.  Each pair r draws
        θ_r ~ Uniform[theta - delta, theta + delta] independently.
        Set delta=0 for a fixed rotation angle (no per-pair randomness).
        Per-pair random θ gives an uncountably infinite reconstruction
        orbit; fixed θ gives an orbit of size n-1.
    mix_prob : float in (0, 1], default 0.75
        Probability of using angle θ_r (vs π−θ_r) for each pair when the
        mixed-angle scheme is active (see below).
        mix_prob=0.75 : default — P(correct recovery)=0.75 for ±1/categorical
                        columns, near-fixed-θ ML utility for continuous.
        mix_prob=0.5  : perfect-privacy scheme — P(correct)=0.5.
        mix_prob=1.0  : equivalent to force_mixed_angle=False (fixed θ always).
    categorical_cols : None | [] | list[int], default None
        Controls which columns are treated as nominal categorical variables
        via the encode–rotate–decode pipeline.

        None (default)
            Auto-detect: any column whose values cannot be cast to float
            (string / object dtype) is automatically identified and passed
            through encode–decode.  Purely numeric arrays are unaffected.
        [] (empty list)
            Skip encode–decode entirely.  All columns are treated as
            numeric (continuous or pre-coded ±1).  Use this when the data
            are already fully numeric.
        [i, j, …]
            Treat exactly these column indices as nominal categorical via
            encode–decode.  Any additional string columns not already in
            the list are also auto-detected and appended (safety net).

        Note: the mixed-angle {θ, π−θ} privacy scheme always applies
        to every row pair regardless of column types or delta value.
        This unified scheme covers continuous, ±1-coded, and encoded-
        categorical columns identically; no auto-detection of column
        types is performed.  Set force_mixed_angle=False to disable it.
    n_threshold : int, default 400
        Crossover for dense vs sparse Householder implementation.
    return_key : bool, default False
        If True, also return the key dict needed by sphere_recover().
    force_mixed_angle : bool or None, default None
        None or True : use the unified mixed-angle scheme — each pair
                independently selects θ_r or π−θ_r with probability
                (mix_prob, 1−mix_prob).  Applies to ALL column types.
        False        : disable mixed-angle; all sign multipliers = +1
                (equivalent to mix_prob=1.0).

    Returns
    -------
    Zstar : (n, p) array
    key   : dict (only if return_key=True)
    """
    Z = np.asarray(Z)

    # ── Determine nominal categorical columns ─────────────────────────────────
    if categorical_cols is None:
        # Auto-detect: columns that cannot be cast to float
        nom_cols = _detect_string_cols(Z)
    else:
        nom_cols = list(categorical_cols)
        if nom_cols:
            # Also catch any remaining string columns the user didn't list
            extra = [j for j in _detect_string_cols(Z) if j not in set(nom_cols)]
            if extra:
                import warnings
                warnings.warn(
                    f"sphere: auto-detected additional string column(s) {extra} "
                    f"not in categorical_cols; adding them to encode-decode.",
                    stacklevel=2)
                nom_cols = nom_cols + extra

    # ── encode_decode: expand → SPHERE → decode ──────────────────────────────
    if nom_cols:
        # 1. Encode: each K-class col → K ±1 indicator cols
        #    _encode_categoricals handles mixed-type arrays column-by-column.
        Z_enc, enc_info = _encode_categoricals(Z, nom_cols)

        # 2. SPHERE on expanded matrix with categorical_cols=[] (pure numeric).
        #    The unified mixed-angle scheme applies to ALL pairs (including
        #    the ±1 indicator columns), which is essential for privacy:
        #    fixed θ alone gives a deterministic decode (correct category
        #    always scores positive for θ < π/4), leaking the original label.
        #    Mixed-angle breaks this — P(correct recovery) = mix_prob.
        inner_result = sphere(
            Z_enc, theta=theta, delta=delta, mix_prob=mix_prob,
            categorical_cols=[],        # expanded matrix is pure numeric
            n_threshold=n_threshold,
            return_key=return_key,
            force_mixed_angle=force_mixed_angle)

        if return_key:
            Z_enc_star, inner_key = inner_result
        else:
            Z_enc_star = inner_result
            inner_key  = None

        # 3. Decode: K continuous scores → exact-count categorical column
        Zstar = _decode_categoricals(Z_enc_star, enc_info)

        if return_key:
            key = {
                "mode":      "encode_decode",
                "enc_info":  enc_info,
                "inner_key": inner_key,
                "theta":     theta,
                "delta":     delta,
                "n":         int(Z.shape[0]),
            }
            return Zstar, key
        return Zstar

    n, p = Z.shape
    Zt = Z.astype(float, copy=True)
    c = float(np.cos(theta))
    s = float(np.sin(theta))

    # Step 1 — random pairing (secret)
    idx = np.random.permutation(n)
    pi = idx[0::2][:n // 2]
    pj = idx[1::2][:n // 2]
    k = n // 2

    # Per-pair angle: draw k offsets ε_r ~ Uniform(-delta, delta), add to theta.
    # delta=0 reduces to the original fixed-angle behaviour (scalar c, s).
    if delta > 0:
        eps_k   = np.random.uniform(-delta, delta, k)   # (k,) offsets in radians
        thetas_k = theta + eps_k
        c_rot   = np.cos(thetas_k)   # (k,) array
        s_rot   = np.sin(thetas_k)   # (k,) array
    else:
        thetas_k = None
        c_rot    = c   # scalar
        s_rot    = s   # scalar

    key = {
        "idx":      idx,
        "theta":    theta,
        "delta":    delta,
        "thetas_k": thetas_k,
        "n":        n,
        "mode":     "orthogonal",
    }

    if True:  # orthogonal rotation (only path remaining after encode_decode dispatch)
        # Step 2 — angle scheme.
        # mix_prob always applies to every pair regardless of delta or column
        # types: each pair independently selects θ_r or π−θ_r with probability
        # (mix_prob, 1−mix_prob).  This unified scheme covers continuous,
        # ±1-coded, and encoded-categorical columns identically.
        # Override with force_mixed_angle=False to disable (all signs = +1).
        if force_mixed_angle is False:
            signs = np.ones(k)
        else:
            signs = np.where(np.random.rand(k) < mix_prob, 1.0, -1.0)

        key["signs"] = signs
        key["mixed_angle"] = (force_mixed_angle is not False)
        key["mix_prob"] = mix_prob

        # Step 3 — Givens applied to ALL columns uniformly
        Zt = _givens_orthogonal(Zt, pi, pj, c_rot, s_rot, signs)

        # Step 3b — Odd-n fix: rotate the unpaired row with a random partner.
        # Picks uniformly from the n-1 already-paired rows (idx[0:n-1]).
        if n % 2 == 1:
            extra = int(idx[-1])
            rp_pos = int(np.random.randint(0, n - 1))
            r_partner = int(idx[rp_pos])
            sign_e = float(np.where(np.random.rand() < mix_prob, 1.0, -1.0)
                           if force_mixed_angle is not False else 1.0)
            # Per-pair theta for the extra rotation (1 more draw when delta > 0).
            if delta > 0:
                theta_e = float(np.random.uniform(theta - delta, theta + delta))
                c_e     = float(np.cos(theta_e))
                s_e     = float(np.sin(theta_e))
            else:
                theta_e = theta
                c_e     = c
                s_e     = s
            ri_e = Zt[extra].copy()
            rj_e = Zt[r_partner].copy()
            # Rotate the extra row with its partner.  Written with explicit
            # out= buffers rather than the shorthand `c*ri - s*rj` to avoid a
            # numpy scalar-multiply buffer-reuse bug: when a 1-D (or 1×p) array
            # has refcount==1 and its byte size hits certain SIMD thresholds
            # (e.g. p ≥ 32768 under Python 3.14 / numpy 1.26.4), `c * arr`
            # writes the result back into arr's own memory, corrupting it before
            # the second assignment reads it.  `np.multiply(..., out=dest)` with
            # dest ≠ arr forces numpy to use a distinct output buffer.
            _sc_ce     = float(sign_e * c_e)
            _new_extra = np.empty(p, dtype=float)
            _new_rp    = np.empty(p, dtype=float)
            _tmp       = np.empty(p, dtype=float)
            np.multiply(_sc_ce, ri_e, out=_new_extra)          # sign_e*c_e * ri_e
            np.multiply(s_e,    rj_e, out=_tmp)
            np.subtract(_new_extra, _tmp, out=_new_extra)      # -= s_e * rj_e
            np.multiply(s_e,    ri_e, out=_new_rp)             # s_e * ri_e
            np.multiply(_sc_ce, rj_e, out=_tmp)
            np.add(_new_rp, _tmp, out=_new_rp)                 # += sign_e*c_e * rj_e
            Zt[extra]     = _new_extra
            Zt[r_partner] = _new_rp

            # Analytic Householder for odd n.
            # After regular pairs:  w[pi[r]] = signs[r]*c_r − s_r,
            #                       w[pj[r]] = signs[r]*c_r + s_r,
            #                       w[extra] = 1.
            # The extra Givens updates only w[extra] and w[r_partner]; all
            # other entries are unchanged from the even-n analytic formula.
            #
            # Determine w_r = (Ṽᵀ·1)[r_partner] before the extra rotation.
            # For per-pair theta, use c_rot[pair_r] / s_rot[pair_r].
            pair_r = rp_pos // 2          # which pair r_partner belongs to
            is_pj  = (rp_pos % 2 == 1)   # True → r_partner is in pj; False → pi
            if delta > 0:
                c_pr = float(c_rot[pair_r])
                s_pr = float(s_rot[pair_r])
            else:
                c_pr, s_pr = c, s
            w_r = float(signs[pair_r] * c_pr + s_pr
                        if is_pj else signs[pair_r] * c_pr - s_pr)

            # Updated entries after extra Givens:
            w_extra_new   = sign_e * c_e * 1.0 - s_e * w_r
            w_partner_new = s_e * 1.0 + sign_e * c_e * w_r

            # Build v = w − 1 analytically.
            # Start from the even-n analytic v and patch the two changed rows.
            v_hh = np.zeros(n)
            v_hh[pi] = signs * c_rot - s_rot - 1.0
            v_hh[pj] = signs * c_rot + s_rot - 1.0
            v_hh[extra]     = w_extra_new - 1.0
            v_hh[r_partner] = w_partner_new - 1.0   # overwrite old entry

            norm2 = float(np.dot(v_hh, v_hh))
            if norm2 > 1e-24:
                proj = v_hh @ Zt
                Zt -= (2.0 / norm2) * np.outer(v_hh, proj)

            key["odd_extra"] = (extra, r_partner, sign_e, c_e, s_e,
                                w_extra_new, w_partner_new)

        else:
            # Step 4 — Householder mean correction (analytic, even n only)
            Zt = _householder_orthogonal(Zt, pi, pj, idx, n, c_rot, s_rot, signs, n_threshold)

    if return_key:
        return Zt, key
    return Zt


def sphere_recover(Zstar, key):
    """Invert SPHERE using the stored key.

    For "orthogonal" mode: undo Householder then undo the per-pair
    Givens (equivalent to rotating by −θ^(r) for each pair).
    For "encode_decode" mode: re-encode the output to recover the
    continuous inner SPHERE result, invert it, then re-decode categoricals.
    """
    mode = key.get("mode", "orthogonal")

    if mode == "encode_decode":
        # encode_decode is not invertible for categorical columns (decode is
        # a many-to-one map). We recover the continuous columns exactly by
        # inverting the inner orthogonal SPHERE, then re-decode categoricals.
        inner_key = key["inner_key"]
        enc_info  = key["enc_info"]
        Z_enc_rec = sphere_recover(Zstar_enc := _encode_categoricals(
            Zstar, [info['orig'] for info in enc_info
                    if info['type'] == 'cat'])[0],
            inner_key)
        return _decode_categoricals(Z_enc_rec, enc_info)

    idx = key["idx"]
    theta = key["theta"]
    n = key["n"]
    c = float(np.cos(theta))
    s = float(np.sin(theta))
    pi = idx[0::2][:n // 2]
    pj = idx[1::2][:n // 2]
    Zt = Zstar.astype(float, copy=True)

    # Reconstruct per-pair c_rot / s_rot (or scalar for delta=0 / old keys).
    thetas_k = key.get("thetas_k", None)
    if thetas_k is not None:
        c_rot = np.cos(thetas_k)
        s_rot = np.sin(thetas_k)
    else:
        c_rot = c   # scalar
        s_rot = s   # scalar

    if mode == "orthogonal":
        signs = key["signs"]

        if "odd_extra" in key:
            # Odd n: undo analytic Householder, extra Givens, regular Givens.
            # Support both old 5-tuple (no c_e/s_e) and new 7-tuple formats.
            oe = key["odd_extra"]
            if len(oe) == 7:
                extra, r_partner, sign_e, c_e, s_e, w_extra_new, w_partner_new = oe
            else:
                extra, r_partner, sign_e, w_extra_new, w_partner_new = oe
                c_e, s_e = c, s   # old key: fixed theta
            # Rebuild v_hh from stored values (same formula as forward pass)
            v_hh = np.zeros(n)
            v_hh[pi] = signs * c_rot - s_rot - 1.0
            v_hh[pj] = signs * c_rot + s_rot - 1.0
            v_hh[extra]     = w_extra_new - 1.0
            v_hh[r_partner] = w_partner_new - 1.0
            norm2 = float(np.dot(v_hh, v_hh))
            if norm2 > 1e-24:
                proj = v_hh @ Zt
                Zt -= (2.0 / norm2) * np.outer(v_hh, proj)
            # Undo extra Givens: G^T  (negate sin term)
            ri_e = Zt[extra].copy()
            rj_e = Zt[r_partner].copy()
            Zt[extra]     = sign_e * c_e * ri_e + s_e * rj_e
            Zt[r_partner] = -s_e * ri_e + sign_e * c_e * rj_e
            # Undo regular Givens (per-pair or scalar)
            ri, rj = Zt[pi].copy(), Zt[pj].copy()
            if np.ndim(c_rot) == 0:
                sc = signs[:, None] * c_rot
                Zt[pi] = sc * ri + s_rot * rj
                Zt[pj] = -s_rot * ri + sc * rj
            else:
                sc  = (signs * c_rot)[:, None]
                s_b = s_rot[:, None]
                Zt[pi] = sc * ri + s_b * rj
                Zt[pj] = -s_b * ri + sc * rj
        else:
            # Even n: analytic Householder + regular Givens
            Zt = _householder_orthogonal(Zt, pi, pj, idx, n, c_rot, s_rot, signs, n_threshold=400)
            ri, rj = Zt[pi].copy(), Zt[pj].copy()
            if np.ndim(c_rot) == 0:
                sc = signs[:, None] * c_rot
                Zt[pi] = sc * ri + s_rot * rj
                Zt[pj] = -s_rot * ri + sc * rj
            else:
                sc  = (signs * c_rot)[:, None]
                s_b = s_rot[:, None]
                Zt[pi] = sc * ri + s_b * rj
                Zt[pj] = -s_b * ri + sc * rj
        return Zt


def sphere_with_missing(Z, theta=np.pi / 6, delta=5.0 * np.pi / 180.0,
                        mix_prob=0.75,
                        categorical_cols=None,
                        impute="mean", n_threshold=400, return_key=False):
    """SPHERE for data with block-missing entries (NaN-encoded).

    Missing values should be encoded as ``np.nan``. The algorithm groups
    observations by their observed variable pattern (missingness pattern),
    imputes singleton rows into the nearest multi-row site, then applies
    standard SPHERE independently within each site. The output masks all
    entries that were originally missing, preserving the sparsity pattern.

    Reference:
        SORSD with Missing Data — Block Missingness Handling.
        Algorithm 1 (sorsd_specialcases.tex).

    Parameters
    ----------
    Z : (n, p) array
        Real data, with ``np.nan`` indicating missing entries.
    theta, categorical_cols, n_threshold, return_key
        Forwarded to ``sphere`` on each site's complete submatrix.
        See ``sphere`` for the semantics of ``categorical_cols``.
    impute : {"mean", "zero"}  (default "mean")
        How to fill a singleton's missing values before merging into the
        nearest site. "mean" uses the global column mean (over all observed
        entries across all sites). "zero" fills with zero — only appropriate
        when all columns are centered.

    Returns
    -------
    Zstar : (n, p) array with the same NaN pattern as input
    key   : dict with per-site keys (only if return_key=True)
        {
          "site_keys": [{site_mask, cols, inner_key}, ...],
          "n": n, "p": p,
          "nan_mask": original boolean missing-mask,
        }

    Guarantees within each site s:
        - Z*_s' Z*_s  =  Z_s' Z_s           (cross-product matrix)
        - mean(Z*_s)  =  mean(Z_s)          (column means)
        - OLS inference on Z*_s identical to OLS on Z_s
        - Privacy guarantee per sphere()
    """
    Z = np.asarray(Z, dtype=float)
    n, p = Z.shape
    nan_mask = np.isnan(Z)
    if not nan_mask.any():
        # No missing values → delegate directly to sphere()
        return sphere(Z, theta=theta, delta=delta, mix_prob=mix_prob,
                      categorical_cols=categorical_cols,
                      n_threshold=n_threshold,
                      return_key=return_key)

    # Step 1 — global column means (over all observed entries)
    col_means = np.zeros(p)
    for k in range(p):
        observed = ~nan_mask[:, k]
        if observed.any():
            col_means[k] = float(np.nanmean(Z[observed, k]))
        else:
            col_means[k] = 0.0

    # Step 2 — group rows by observed-variable pattern
    # Each row's pattern is a tuple of observed column indices.
    obs_patterns = [tuple(np.where(~nan_mask[i])[0]) for i in range(n)]
    site_map: dict[tuple, list[int]] = {}
    for i, patt in enumerate(obs_patterns):
        site_map.setdefault(patt, []).append(i)

    # Separate singletons from multi-row sites
    singletons = [(i, patt) for patt, rows in site_map.items()
                  if len(rows) == 1 for i in rows]
    multi_sites = {patt: rows for patt, rows in site_map.items()
                   if len(rows) >= 2}

    # If no multi-row site exists (pathological), merge the two singletons
    # with largest pattern overlap.
    if not multi_sites:
        raise ValueError(
            "No site has ≥2 observations after grouping by missingness "
            "pattern. Block-missingness SPHERE is not applicable.")

    # Step 3 — merge each singleton into the most-overlapping multi site
    row_to_site: dict[int, tuple] = {}
    site_row_patterns: dict[tuple, list[int]] = {}  # original observed cols
    for patt, rows in multi_sites.items():
        for r in rows:
            row_to_site[r] = patt
            site_row_patterns[patt] = rows

    for i, patt in singletons:
        # Find multi site with max |M_i ∩ M_s|
        best = None
        best_overlap = -1
        patt_set = set(patt)
        for s_patt in multi_sites:
            ov = len(patt_set & set(s_patt))
            if ov > best_overlap:
                best_overlap = ov
                best = s_patt
        # Merge into best (row i now adopts M_s pattern via imputation)
        multi_sites[best].append(i)
        row_to_site[i] = best

    # Step 4 — apply SPHERE to each site's complete submatrix
    Zstar = Z.copy()
    site_keys = []
    for patt, rows in sorted(multi_sites.items(), key=lambda x: -len(x[1])):
        rows_arr = np.asarray(sorted(rows), dtype=int)
        cols_arr = np.asarray(patt, dtype=int)
        if rows_arr.size < 2 or cols_arr.size == 0:
            continue

        # Build site matrix: complete sub-block of Z over (rows × patt),
        # with any NaN in merged singletons filled via the chosen imputation.
        sub = Z[np.ix_(rows_arr, cols_arr)].copy()
        if impute == "mean":
            for col_idx, k in enumerate(cols_arr):
                col = sub[:, col_idx]
                missing = np.isnan(col)
                if missing.any():
                    col[missing] = col_means[k]
                sub[:, col_idx] = col
        elif impute == "zero":
            sub[np.isnan(sub)] = 0.0
        else:
            raise ValueError(f"impute must be 'mean' or 'zero', got {impute!r}")

        # Forward categorical_cols restricted to this site's column indices.
        # categorical_cols=None → auto-detect within each site's submatrix.
        # categorical_cols=[...] → map global indices to local indices.
        if categorical_cols is None:
            site_cat = None   # let sphere() auto-detect per site
        elif len(categorical_cols) == 0:
            site_cat = []     # explicitly no categorical handling
        else:
            cat_set = set(categorical_cols)
            site_cat = [local for local, orig in enumerate(cols_arr)
                        if int(orig) in cat_set]

        result = sphere(sub, theta=theta, delta=delta, mix_prob=mix_prob,
                        categorical_cols=site_cat,
                        n_threshold=n_threshold,
                        return_key=return_key)
        if return_key:
            sub_star, inner_key = result
        else:
            sub_star = result
            inner_key = None

        # Write synthetic values back to the full matrix
        Zstar[np.ix_(rows_arr, cols_arr)] = sub_star

        if return_key:
            site_keys.append({
                "rows": rows_arr,
                "cols": cols_arr,
                "pattern": patt,
                "inner_key": inner_key,
            })

    # Step 5 — mask originally-missing entries in the output
    Zstar[nan_mask] = np.nan

    if return_key:
        key = {
            "n": n, "p": p,
            "nan_mask": nan_mask,
            "col_means": col_means,
            "site_keys": site_keys,
        }
        return Zstar, key
    return Zstar


def sphere_postprocess(Zstar, Z_orig, pct_lo=1, pct_hi=99,
                       categorical_cols=None):
    """Optional post-processing of the synthetic matrix.

    Step 1 — Percentile clip each column of Z* to the [pct_lo, pct_hi]
             percentiles of the original column. Protects extreme-value
             privacy and enforces natural bounds.
    Step 2 — Integer rounding for integer-valued continuous columns with
             > 2 unique values.

    IMPORTANT: columns listed in `categorical_cols` are NEVER rounded,
    because under the "orthogonal" mode their output is a continuous
    relaxation by design — rounding would break the privacy guarantee.
    """
    Zpp = Zstar.astype(float, copy=True)
    p = Zpp.shape[1]
    cat_set = set(categorical_cols or [])

    for j in range(p):
        col_orig = Z_orig[:, j]
        lo = np.percentile(col_orig, pct_lo)
        hi = np.percentile(col_orig, pct_hi)
        Zpp[:, j] = np.clip(Zpp[:, j], lo, hi)

        if j in cat_set:
            continue
        uniq = np.unique(col_orig)
        is_integer = np.allclose(col_orig, np.round(col_orig)) and len(uniq) > 2
        if is_integer:
            Zpp[:, j] = np.round(Zpp[:, j])
    return Zpp


# ────────────────────────────────────────────────────────────────────────────
# Self-test
# ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import time

    print("=== Verification — pure numeric (no categorical) ===")
    for n, p in [(300, 20), (1000, 50), (5000, 100)]:
        Z = np.random.randn(n, p) + 2.0
        np.random.seed(42)
        Zs = sphere(Z)   # categorical_cols=None, but Z is float → auto-detects nothing
        print(f"  n={n:>5}, p={p:>3} | "
              f"mean={np.abs(Zs.mean(0) - Z.mean(0)).max():.1e}  "
              f"Z'Z={np.abs(Zs.T @ Zs - Z.T @ Z).max():.1e}  "
              f"var={np.abs(Zs.var(0) - Z.var(0)).max():.1e}")

    print("\n=== Verification — mixed continuous + binary ±1 ===")
    n, p = 1000, 20
    Z = np.random.randn(n, p) + 2.0
    Z[:, 5] = np.random.choice([-1.0, 1.0], size=n)
    Z[:, 10] = np.random.choice([-1.0, 1.0], size=n)
    np.random.seed(7)
    Zs = sphere(Z)   # unified mixed-angle, default delta=5°; no encode-decode
    print(f"  mean err = {np.abs(Zs.mean(0) - Z.mean(0)).max():.1e}")
    print(f"  Z'Z err  = {np.abs(Zs.T @ Zs - Z.T @ Z).max():.1e}")
    print(f"  binary col 5 unique vals in Z*: {np.unique(np.round(Zs[:, 5], 3))[:8]}")

    print("\n=== Verification — encode_decode (explicit categorical_cols) ===")
    for K in (2, 3, 5):
        n, p = 800, 12
        Z = np.random.randn(n, p)
        cat_col = 4
        cats = np.random.choice(K, n).astype(float)
        Z[:, cat_col] = cats
        orig_counts = np.bincount(cats.astype(int), minlength=K)
        np.random.seed(42)
        Zs = sphere(Z, categorical_cols=[cat_col])
        synth_counts = np.bincount(Zs[:, cat_col].astype(int), minlength=K)
        cont = [j for j in range(p) if j != cat_col]
        ztze = np.abs(Zs[:, cont].T @ Zs[:, cont] -
                      Z[:, cont].T @ Z[:, cont]).max()
        mean_err = np.abs(Zs[:, cont].mean(0) - Z[:, cont].mean(0)).max()
        print(f"  K={K}: exact_counts={np.array_equal(orig_counts, synth_counts)}  "
              f"orig={orig_counts}  synth={synth_counts}  "
              f"cont_ZtZ_err={ztze:.1e}  mean_err={mean_err:.1e}")

    print("\n=== Verification — encode_decode (auto-detect string cols) ===")
    n, p = 600, 5
    Z_obj = np.empty((n, p), dtype=object)
    Z_obj[:, :3] = np.random.randn(n, 3)
    cats_a = np.random.choice(['cat', 'dog', 'bird'], n)
    cats_b = np.random.choice(['low', 'high'], n)
    Z_obj[:, 3] = cats_a
    Z_obj[:, 4] = cats_b
    np.random.seed(1)
    Zs = sphere(Z_obj)   # categorical_cols=None → auto-detects cols 3 and 4
    orig_a = dict(zip(*np.unique(cats_a, return_counts=True)))
    syn_a  = dict(zip(*np.unique(Zs[:, 3], return_counts=True)))
    orig_b = dict(zip(*np.unique(cats_b, return_counts=True)))
    syn_b  = dict(zip(*np.unique(Zs[:, 4], return_counts=True)))
    print(f"  col3 (3-class string): counts match = {orig_a == syn_a}")
    print(f"  col4 (2-class string): counts match = {orig_b == syn_b}")

    print("\n  Multi-column test (K=3 + K=4 explicit):")
    n, p = 600, 10
    Z = np.random.randn(n, p)
    Z[:, 2] = np.random.choice(3, n).astype(float)
    Z[:, 7] = np.random.choice(4, n).astype(float)
    orig_c2 = np.bincount(Z[:, 2].astype(int), minlength=3)
    orig_c7 = np.bincount(Z[:, 7].astype(int), minlength=4)
    np.random.seed(0)
    Zs = sphere(Z, categorical_cols=[2, 7])
    syn_c2 = np.bincount(Zs[:, 2].astype(int), minlength=3)
    syn_c7 = np.bincount(Zs[:, 7].astype(int), minlength=4)
    print(f"  col2 exact={np.array_equal(orig_c2, syn_c2)}  "
          f"orig={orig_c2}  synth={syn_c2}")
    print(f"  col7 exact={np.array_equal(orig_c7, syn_c7)}  "
          f"orig={orig_c7}  synth={syn_c7}")

    print("\n=== Timing (100 runs each) ===")
    print(f"{'n':>6} {'p':>4} | {'100 runs (ms)':>14}")
    print("-" * 40)
    for n, p in [(300, 20), (1000, 50), (5000, 100), (10000, 200)]:
        Z = np.random.randn(n, p) + 2.0
        t0 = time.perf_counter()
        for seed in range(100):
            np.random.seed(seed)
            sphere(Z.copy(), categorical_cols=[])
        total_ms = (time.perf_counter() - t0) * 1000
        print(f"{n:>6} {p:>4} | {total_ms:>14.1f}")

    print("\n=== Round-trip (sphere_recover) ===")
    n, p = 500, 15
    Z = np.random.randn(n, p) + 2.0
    np.random.seed(123)
    Zs, key = sphere(Z, categorical_cols=[], return_key=True)
    Zrec = sphere_recover(Zs, key)
    err = np.abs(Zrec - Z).max()
    print(f"  recover err = {err:.2e}")
