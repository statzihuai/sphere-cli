#!/usr/bin/env bash
# Build a signed macOS .pkg installer for the sphere CLI.
#
# Called by build.sh:
#   bash pkg/build-pkg.sh "$IDENTITY"
#
# Requires: pkgbuild, productbuild (Xcode Command Line Tools)
# Output:   dist/SPHERE-CLI.pkg
set -euo pipefail
cd "$(dirname "$0")/.."   # always run from sphere-cli root

IDENTITY="${1:-}"
VERSION="0.1.0"
IDENTIFIER="com.sphere.cli"
INSTALL_LIB="/usr/local/lib/sphere-cli"
INSTALL_BIN="/usr/local/bin"
PKG_NAME="SPHERE-CLI"

STAGING="dist/_pkg-staging"
SCRIPTS_DIR="pkg/scripts"
COMPONENT_PKG="dist/${PKG_NAME}-component.pkg"
FINAL_PKG="dist/${PKG_NAME}.pkg"

# ── Clean previous staging ────────────────────────────────────────────────────
rm -rf "$STAGING" "$COMPONENT_PKG" "$FINAL_PKG"
mkdir -p "${STAGING}${INSTALL_LIB}"
mkdir -p "${STAGING}${INSTALL_BIN}"

# ── Copy the bundle into staging ──────────────────────────────────────────────
echo "==> Staging dist/sphere-cli → ${INSTALL_LIB}"
cp -R dist/sphere-cli/. "${STAGING}${INSTALL_LIB}/"

# ── Symlink stub (resolved by postinstall script) ─────────────────────────────
# pkgbuild does not follow symlinks well; we create the symlink at postinstall.

# ── Scripts: postinstall creates symlink ──────────────────────────────────────
mkdir -p "$SCRIPTS_DIR"
cat > "${SCRIPTS_DIR}/postinstall" <<'POSTINSTALL'
#!/bin/sh
set -e
ln -sf /usr/local/lib/sphere-cli/sphere /usr/local/bin/sphere
chmod +x /usr/local/lib/sphere-cli/sphere
POSTINSTALL
chmod +x "${SCRIPTS_DIR}/postinstall"

# ── Build component .pkg ──────────────────────────────────────────────────────
echo "==> Building component package …"
pkgbuild \
  --root    "$STAGING" \
  --scripts "$SCRIPTS_DIR" \
  --identifier "$IDENTIFIER" \
  --version    "$VERSION" \
  --install-location "/" \
  "$COMPONENT_PKG"

# ── Build distribution .pkg (signed or unsigned) ──────────────────────────────
echo "==> Building distribution package …"
if [[ -n "$IDENTITY" ]]; then
  productbuild \
    --package "$COMPONENT_PKG" \
    --sign    "Developer ID Installer: ${IDENTITY}" \
    "$FINAL_PKG" 2>/dev/null || \
  productbuild \
    --package "$COMPONENT_PKG" \
    "$FINAL_PKG"
else
  productbuild \
    --package "$COMPONENT_PKG" \
    "$FINAL_PKG"
fi

# ── Cleanup ───────────────────────────────────────────────────────────────────
rm -rf "$STAGING" "$COMPONENT_PKG"

SIZE=$(du -sh "$FINAL_PKG" | cut -f1)
echo "✓ ${FINAL_PKG}  (${SIZE})"
echo "  Double-click to install; sphere will be available at /usr/local/bin/sphere"
