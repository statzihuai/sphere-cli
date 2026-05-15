#!/usr/bin/env bash
# Build the sphere CLI binary.
#
# Usage:
#   ./build.sh              build for current platform
#   ./build.sh --sign       build + codesign (macOS, requires Developer ID)
#   ./build.sh --pkg        build + codesign + create macOS .pkg installer
#
# Output (macOS):
#   dist/sphere-cli/sphere     — the raw binary (add to PATH)
#   dist/SPHERE-CLI.pkg        — double-click installer (with --pkg)
#
# Output (Linux):
#   dist/sphere-cli/sphere     — the raw binary
#   dist/install.sh            — curl-able install script (with --pkg)
set -euo pipefail
cd "$(dirname "$0")"

SIGN=false; PKG=false
for arg in "$@"; do
  [[ "$arg" == "--sign" ]] && SIGN=true
  [[ "$arg" == "--pkg"  ]] && { PKG=true; SIGN=true; }
done

# ── Step 1: compile proprietary modules with Cython ──────────────────────────
echo "==> Compiling sphere_cli modules with Cython …"
# Clean stale .so and .c artefacts first
find sphere_cli -name "*.so" -o -name "*.c" | xargs rm -f 2>/dev/null || true
python3 setup_cython.py build_ext --inplace --quiet

# ── Step 2: hide .py sources so PyInstaller uses the .so files ────────────────
PROTECTED=( _algo _core _generate _evaluate _certify cli )
for m in "${PROTECTED[@]}"; do
  [[ -f "sphere_cli/${m}.py" ]] && mv "sphere_cli/${m}.py" "sphere_cli/${m}.py.bak"
done
trap 'for m in "${PROTECTED[@]}"; do
        [[ -f "sphere_cli/${m}.py.bak" ]] && mv "sphere_cli/${m}.py.bak" "sphere_cli/${m}.py"
      done
      find sphere_cli -name "*.c" | xargs rm -f 2>/dev/null || true' EXIT

# ── Step 3: PyInstaller ───────────────────────────────────────────────────────
echo "==> Bundling with PyInstaller …"
python3 -m PyInstaller sphere-cli.spec --noconfirm

BINARY="dist/sphere-cli/sphere"

# ── Step 3b: bundle sphere-eval sidecar ──────────────────────────────────────
# SPHERE_EVAL_SRC can be overridden (e.g. in CI where the main repo is checked
# out alongside sphere-cli).  Default: sibling python-sidecar build output.
SPHERE_EVAL_SRC="${SPHERE_EVAL_SRC:-../python-sidecar/dist/sphere-eval}"
if [[ -d "$SPHERE_EVAL_SRC" && -f "$SPHERE_EVAL_SRC/sphere-eval" ]]; then
  echo "==> Bundling sphere-eval from $SPHERE_EVAL_SRC …"
  cp -r "$SPHERE_EVAL_SRC" "dist/sphere-cli/sphere-eval"
  chmod +x "dist/sphere-cli/sphere-eval/sphere-eval"
  echo "    sphere-eval bundled."
else
  echo "WARNING: sphere-eval not found at $SPHERE_EVAL_SRC" >&2
  echo "         Set SPHERE_EVAL_SRC or run python-sidecar/build.sh first." >&2
fi

# ── Step 3d: fix polars runtime ───────────────────────────────────────────────
# PyInstaller deduplicates abi3 .so files by filename and may pick up a
# stale/incompatible version from its binary cache or from the sidecar.
# Force-replace with the correct Python 3.14 runtime.
PLR_SRC="$(python3 -c "import _polars_runtime_32._polars_runtime as m; print(m.__file__)")"
PLR_DST="dist/sphere-cli/_internal/_polars_runtime_32/_polars_runtime.abi3.so"
if [[ -f "$PLR_SRC" && -f "$PLR_DST" ]]; then
  if ! cmp -s "$PLR_SRC" "$PLR_DST"; then
    echo "==> Replacing polars runtime with correct Python 3.14 version …"
    cp "$PLR_SRC" "$PLR_DST"
  fi
fi

SIZE=$(du -sh "dist/sphere-cli" | cut -f1)
echo "    Bundle size: $SIZE"

# ── Step 4: codesign (macOS) ──────────────────────────────────────────────────
if [[ "$SIGN" == true && "$(uname)" == "Darwin" ]]; then
  IDENTITY=$(security find-identity -v -p codesigning \
    | grep "Developer ID Application" | head -1 \
    | sed 's/.*) //' | sed 's/ ".*$//')
  if [[ -z "$IDENTITY" ]]; then
    echo "Warning: no Developer ID Application cert found; skipping codesign."
    SIGN=false
  else
    echo "==> Codesigning with: $IDENTITY"
    find "dist/sphere-cli" -type f \( -name "*.so" -o -name "*.dylib" \) \
      -exec codesign --force --sign "$IDENTITY" --timestamp {} \;
    codesign --force --sign "$IDENTITY" --timestamp --deep "$BINARY"
    echo "    Codesign done."
  fi
fi

# ── Step 5: macOS .pkg installer ─────────────────────────────────────────────
if [[ "$PKG" == true && "$(uname)" == "Darwin" ]]; then
  bash pkg/build-pkg.sh "$IDENTITY"
fi

# ── Step 6: install.sh (always bundled alongside the binary) ─────────────────
# Copied unconditionally so that dist/ is self-contained: users can tar up
# dist/ and run install.sh --prefix ~/.local anywhere (Linux or macOS).
cp install.sh dist/install.sh
echo "==> install.sh written to dist/install.sh"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "✓ Build complete."
echo "  Binary : $(pwd)/$BINARY"
echo "  Size   : $SIZE"
echo ""
echo "  Quick test:"
echo "    dist/sphere-cli/sphere --help"
echo ""
echo "  Install locally:"
if [[ "$(uname)" == "Darwin" ]]; then
  echo "    sudo cp -r dist/sphere-cli /usr/local/lib/"
  echo "    sudo ln -sf /usr/local/lib/sphere-cli/sphere /usr/local/bin/sphere"
else
  echo "    sudo bash dist/install.sh   (copies to /usr/local/lib + /usr/local/bin)"
fi
