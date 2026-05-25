#!/usr/bin/env sh
# SPHERE CLI installer — Linux / macOS
#
# Usage
# -----
#   Simplest (downloads correct build automatically):
#     curl -fsSL https://github.com/statzihuai/sphere-cli/releases/latest/download/install.sh | sh
#
#   User-local install — no sudo, works on HPC / cloud:
#     curl -fsSL https://github.com/statzihuai/sphere-cli/releases/latest/download/install.sh | sh -s -- --prefix ~/.local
#
#   From a local dist/ folder after building:
#     sh install.sh --prefix ~/.local
#
#   Uninstall:
#     sh install.sh --uninstall [--prefix ~/.local]
#
# After a user-local install add to your shell's startup file:
#   export PATH="$HOME/.local/bin:$PATH"   # bash / zsh → ~/.bashrc or ~/.zshrc
#
# Environment variables (alternative to flags):
#   SPHERE_PREFIX        override --prefix
#   SPHERE_VERSION       pin a release tag, e.g. "v0.1.0" (default: latest)
#   SPHERE_BUNDLE_URL    full URL to a sphere-cli-*.tar.gz (skips auto-detect)
#   SPHERE_GITHUB_REPO   override the GitHub repo (default: YOUR_ORG/sphere-cli)

set -e

GITHUB_REPO="${SPHERE_GITHUB_REPO:-statzihuai/sphere-cli}"

# ── Parse arguments ───────────────────────────────────────────────────────────
PREFIX=""
VERSION="${SPHERE_VERSION:-latest}"
UNINSTALL=false

prev=""
for arg in "$@"; do
  case "$arg" in
    --prefix=*) PREFIX="${arg#--prefix=}" ;;
    --prefix)   ;;
    --uninstall) UNINSTALL=true ;;
    --version=*) VERSION="${arg#--version=}" ;;
    --version)  ;;
  esac
  if [ "$prev" = "--prefix"  ]; then PREFIX="$arg"; fi
  if [ "$prev" = "--version" ]; then VERSION="$arg"; fi
  prev="$arg"
done

PREFIX="${SPHERE_PREFIX:-$PREFIX}"
if [ -z "$PREFIX" ]; then
  if [ "$(id -u)" = "0" ]; then PREFIX="/usr/local"
  else                          PREFIX="$HOME/.local"
  fi
fi

INSTALL_LIB="${PREFIX}/lib/sphere-cli"
INSTALL_BIN="${PREFIX}/bin/sphere"

# ── Uninstall ─────────────────────────────────────────────────────────────────
if [ "$UNINSTALL" = "true" ]; then
  echo "Uninstalling SPHERE CLI from ${PREFIX} …"
  rm -rf "$INSTALL_LIB"
  rm -f  "$INSTALL_BIN"
  echo "✓ Uninstalled."
  exit 0
fi

# ── Detect platform ───────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS-$ARCH" in
  Darwin-arm64)          PLATFORM="macos-arm64"   ;;
  Darwin-x86_64)         PLATFORM="macos-x86_64"  ;;
  Linux-x86_64)          PLATFORM="linux-x86_64"  ;;
  Linux-aarch64|Linux-arm64) PLATFORM="linux-arm64" ;;
  *)
    echo "Unsupported platform: $OS-$ARCH" >&2
    exit 1
    ;;
esac

# ── Locate or download the bundle ─────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo .)"

if [ -d "${SCRIPT_DIR}/sphere-cli" ]; then
  # ── Path 1: running from a local dist/ folder ────────────────────────────
  SRC="${SCRIPT_DIR}/sphere-cli"
  echo "Installing SPHERE CLI from ${SRC} …"

elif [ -n "${SPHERE_BUNDLE_URL:-}" ]; then
  # ── Path 2: explicit tarball URL ─────────────────────────────────────────
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  echo "Downloading from ${SPHERE_BUNDLE_URL} …"
  _download "$SPHERE_BUNDLE_URL" | tar -xz -C "$TMP"
  SRC="${TMP}/sphere-cli"

else
  # ── Path 3: auto-download from GitHub Releases ───────────────────────────
  TARBALL="sphere-cli-${PLATFORM}.tar.gz"
  if [ "$VERSION" = "latest" ]; then
    URL="https://github.com/${GITHUB_REPO}/releases/latest/download/${TARBALL}"
  else
    URL="https://github.com/${GITHUB_REPO}/releases/download/${VERSION}/${TARBALL}"
  fi

  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  echo "Downloading SPHERE CLI ${VERSION} for ${PLATFORM} …"
  echo "  ${URL}"

  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$URL" | tar -xz -C "$TMP"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- "$URL" | tar -xz -C "$TMP"
  else
    echo "Error: curl or wget is required." >&2
    exit 1
  fi
  SRC="${TMP}/sphere-cli"
fi

# ── Install ───────────────────────────────────────────────────────────────────
echo "Installing to ${INSTALL_LIB} …"
mkdir -p "$(dirname "$INSTALL_LIB")" "$(dirname "$INSTALL_BIN")"
rm -rf "$INSTALL_LIB"
cp -R "$SRC" "$INSTALL_LIB"
chmod +x "${INSTALL_LIB}/sphere"
rm -f "$INSTALL_BIN"
ln -sf "${INSTALL_LIB}/sphere" "$INSTALL_BIN"

# ── PATH hint ─────────────────────────────────────────────────────────────────
case ":${PATH}:" in *":${PREFIX}/bin:"*) IN_PATH=true ;; *) IN_PATH=false ;; esac

echo ""
echo "✓ SPHERE CLI installed."
echo "  Binary  : ${INSTALL_BIN}"
echo "  Version : $("${INSTALL_LIB}/sphere" --version 2>&1 || echo 'unknown')"
echo ""

if [ "$IN_PATH" = "false" ] && [ "$PREFIX" != "/usr/local" ] && [ "$PREFIX" != "/usr" ]; then
  SHELL_RC=""
  case "${SHELL:-}" in
    */zsh)  SHELL_RC="~/.zshrc"  ;;
    */bash) SHELL_RC="~/.bashrc" ;;
    *)      SHELL_RC="~/.bashrc or ~/.zshrc" ;;
  esac
  echo "  ── ${PREFIX}/bin is not in \$PATH ──────────────────────────"
  echo "  Add this line to ${SHELL_RC}:"
  echo ""
  echo "    export PATH=\"${PREFIX}/bin:\$PATH\""
  echo ""
  echo "  Then reload your shell:  source ${SHELL_RC}"
  echo "  Or for this session:     export PATH=\"${PREFIX}/bin:\$PATH\""
  echo ""
fi

echo "  Quick start:"
echo "    sphere generate data.csv -o synth.csv"
echo "    sphere evaluate real.csv synth.csv"
echo "    sphere certify  real.csv synth.csv -o report.html"
echo ""
echo "  Run 'sphere --help' for full options."
