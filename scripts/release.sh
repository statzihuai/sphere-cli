#!/usr/bin/env bash
# scripts/release.sh — safe npm publish for sphere-cli
#
# Run from the sphere-cli directory:
#   bash scripts/release.sh
#
# Performs all security + sanity checks before publishing:
#   1. sphere-node.js exists on disk (needed in tarball)
#   2. sphere-node.js is NOT tracked by git (obfuscated source must not be in repo)
#   3. No commits in any history contain sphere-node.js
#   4. Working tree is clean (no uncommitted changes)
#   5. Confirms version and tarball contents with user before publishing

set -euo pipefail

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GRN}✓${NC} $1"; }
fail() { echo -e "${RED}✗  $1${NC}"; exit 1; }
warn() { echo -e "${YLW}⚠  $1${NC}"; }

echo ""
echo "SPHERE CLI — pre-publish safety check"
echo "══════════════════════════════════════"
echo ""

# ── 1. sphere-node.js must exist on disk ─────────────────────────────────────
if [[ ! -f "sphere-node.js" ]]; then
  fail "sphere-node.js not found. Generate it first:\n   cd ../  &&  npm run build  (SPHERE app repo)"
fi
SIZE=$(wc -c < sphere-node.js)
if [[ $SIZE -lt 10000 ]]; then
  fail "sphere-node.js is only ${SIZE} bytes — looks like a loader stub, not the obfuscated bundle.\n   Run a fresh app build to regenerate it."
fi
pass "sphere-node.js present ($(( SIZE / 1024 )) KB)"

# ── 2. sphere-node.js must NOT be tracked by git ─────────────────────────────
if git ls-files --error-unmatch sphere-node.js 2>/dev/null; then
  fail "sphere-node.js is tracked by git! Run:\n   git rm --cached sphere-node.js\n   git commit -m 'security: remove sphere-node.js from git'"
fi
pass "sphere-node.js is gitignored (not tracked)"

# ── 3. No commit in history may contain sphere-node.js ───────────────────────
HISTORY_HITS=$(git log --all --oneline -- sphere-node.js 2>/dev/null | wc -l | tr -d ' ')
if [[ "$HISTORY_HITS" -gt 0 ]]; then
  fail "sphere-node.js found in ${HISTORY_HITS} historical commit(s)!\n   Rewrite history with:\n   git filter-repo --path sphere-node.js --invert-paths --force\n   git push origin main --force && git push origin --tags --force"
fi
pass "No historical commits contain sphere-node.js"

# ── 4. Working tree must be clean ─────────────────────────────────────────────
if [[ -n "$(git status --porcelain | grep -v '?? sphere-node')" ]]; then
  warn "Working tree has uncommitted changes:"
  git status --short | grep -v '?? sphere-node' || true
  echo ""
  read -rp "  Continue anyway? [y/N] " CONFIRM
  [[ "$CONFIRM" =~ ^[Yy]$ ]] || exit 1
else
  pass "Working tree is clean"
fi

# ── 5. Show version + tarball preview ────────────────────────────────────────
VERSION=$(node -p "require('./package.json').version")
echo ""
echo "  Version : v${VERSION}"
echo "  Tarball preview:"
npm pack --dry-run 2>&1 | grep "npm notice" | grep -v "^npm notice$" | sed 's/npm notice /    /'
echo ""

# Abort if sphere-node.js is NOT in the tarball
if ! npm pack --dry-run 2>&1 | grep -q "sphere-node.js"; then
  fail "sphere-node.js is missing from the npm tarball!\n   Check the 'files' field in package.json."
fi
pass "sphere-node.js is in the npm tarball"

# Abort if sphere_cli/.so files are NOT in the tarball
if ! npm pack --dry-run 2>&1 | grep -q "sphere_cli/"; then
  fail "sphere_cli/ (evaluation engine) is missing from the npm tarball!\n   Check the 'files' field in package.json."
fi
pass "sphere_cli/ evaluation engine is in the npm tarball"

# ── Confirm before publishing ─────────────────────────────────────────────────
echo ""
read -rp "  Publish sphere-cli@${VERSION} to npm? [y/N] " CONFIRM
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# ── Publish ───────────────────────────────────────────────────────────────────
echo ""
npm publish --access public

# ── Tag and push ──────────────────────────────────────────────────────────────
echo ""
git tag "v${VERSION}" 2>/dev/null && echo -e "${GRN}✓${NC} Tagged v${VERSION}" || warn "Tag v${VERSION} already exists — skipping"
git push origin "v${VERSION}" 2>/dev/null && echo -e "${GRN}✓${NC} Tag pushed to GitHub" || warn "Tag push failed — push manually: git push origin v${VERSION}"

echo ""
echo -e "${GRN}✓ sphere-cli@${VERSION} published successfully.${NC}"
echo ""
