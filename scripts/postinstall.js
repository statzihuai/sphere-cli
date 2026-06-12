'use strict';

/**
 * SPHERE CLI postinstall — download the platform-specific sealed binary.
 *
 * The SPHERE engine ships as a signed + notarized native binary (one per
 * platform) hosted on GitHub Releases. This script detects the platform,
 * downloads the matching tarball, verifies its SHA-256 against the published
 * SHA256SUMS.txt, and extracts it into ./vendor/. No algorithm source ships in
 * the npm package — only this downloader + a thin launcher.
 *
 * Env:
 *   SPHERE_SKIP_POSTINSTALL=1   skip download (CI / offline)
 *   SPHERE_BINARY_BASEURL=...   override the release base URL (testing)
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

const PKG = require('../package.json');
const VERSION = PKG.version;
const REPO = 'statzihuai/sphere-cli';
// Binary release tag — decoupled from the npm package version so JS-only patch
// releases reuse the same prebuilt/notarized binaries without re-uploading them.
const BINARY_RELEASE = 'v0.2.3';

const PLATFORM = process.platform; // 'darwin' | 'linux'
const ARCH = process.arch;         // 'arm64' | 'x64'
const KEY = `${PLATFORM}-${ARCH}`;
const SUPPORTED = new Set(['darwin-arm64', 'darwin-x64', 'linux-x64']);

const PKG_ROOT = path.join(__dirname, '..');
const VENDOR = path.join(PKG_ROOT, 'vendor');
const ASSET = `sphere-${KEY}.tar.gz`;
const BASE = process.env.SPHERE_BINARY_BASEURL
  || `https://github.com/${REPO}/releases/download/${BINARY_RELEASE}`;

function log(m) { process.stdout.write(`sphere-cli: ${m}\n`); }
function fail(m) { process.stderr.write(`sphere-cli: ${m}\n`); process.exit(1); }

if (process.env.SPHERE_SKIP_POSTINSTALL === '1') {
  log('SPHERE_SKIP_POSTINSTALL=1 — skipping binary download.');
  process.exit(0);
}

if (!SUPPORTED.has(KEY)) {
  // Don't hard-fail the install; the launcher prints guidance if run.
  process.stderr.write(
    `sphere-cli: no prebuilt binary for ${KEY} yet ` +
    `(supported: ${[...SUPPORTED].join(', ')}).\n`,
  );
  process.exit(0);
}

// Already installed for this version?
const STAMP = path.join(VENDOR, '.version');
const BIN = path.join(VENDOR, 'sphere-cli', 'sphere');
if (fs.existsSync(BIN) && fs.existsSync(STAMP) &&
    fs.readFileSync(STAMP, 'utf8').trim() === BINARY_RELEASE) {
  log(`binary already present (${BINARY_RELEASE}).`);
  process.exit(0);
}

function curl(url, dest) {
  execFileSync('curl', ['-fL', '--retry', '3', '--retry-delay', '2',
    '--connect-timeout', '30', '-o', dest, url], { stdio: ['ignore', 'ignore', 'inherit'] });
}

function sha256(file) {
  const h = crypto.createHash('sha256');
  h.update(fs.readFileSync(file));
  return h.digest('hex');
}

try {
  fs.mkdirSync(VENDOR, { recursive: true });
  const tarball = path.join(VENDOR, ASSET);

  log(`downloading ${ASSET} (v${VERSION}) …`);
  curl(`${BASE}/${ASSET}`, tarball);

  // Verify checksum against the published SHA256SUMS.txt
  const sumsFile = path.join(VENDOR, 'SHA256SUMS.txt');
  curl(`${BASE}/SHA256SUMS.txt`, sumsFile);
  const sums = fs.readFileSync(sumsFile, 'utf8');
  const expected = sums.split('\n')
    .map((l) => l.trim().split(/\s+/))
    .find((p) => p[1] && p[1].endsWith(ASSET));
  if (!expected) fail(`no checksum for ${ASSET} in SHA256SUMS.txt`);
  const got = sha256(tarball);
  if (got !== expected[0]) {
    fail(`checksum mismatch for ${ASSET}\n  expected ${expected[0]}\n  got      ${got}`);
  }
  log('checksum verified ✓');

  // Extract
  fs.rmSync(path.join(VENDOR, 'sphere-cli'), { recursive: true, force: true });
  execFileSync('tar', ['-xzf', tarball, '-C', VENDOR], { stdio: 'inherit' });
  fs.chmodSync(BIN, 0o755);
  fs.unlinkSync(tarball);
  fs.rmSync(sumsFile, { force: true });
  fs.writeFileSync(STAMP, BINARY_RELEASE);

  // ── macOS: avoid a painfully slow FIRST run ────────────────────────────────
  // A downloaded, unstapled notarized onedir pays a one-time Gatekeeper check of
  // all ~330 bundled libraries on first launch (looks frozen on "loading pandas").
  // (1) strip quarantine/provenance xattrs → skips the slow *online* assessment;
  // (2) warm the binary now (during install, when waiting is expected) so the
  //     user's first real command is instant. Both are best-effort.
  if (PLATFORM === 'darwin') {
    try { execFileSync('xattr', ['-cr', path.join(VENDOR, 'sphere-cli')], { stdio: 'ignore' }); } catch (_) {}
    try {
      log('warming binary (one-time macOS security scan, ~30–60s) …');
      const warmIn = path.join(VENDOR, '.warm.csv');
      const warmOut = path.join(VENDOR, '.warm_out.csv');
      fs.writeFileSync(warmIn, 'a,b\n1.0,2.0\n3.0,4.0\n5.0,6.0\n');
      execFileSync(BIN, ['generate', warmIn, '-o', warmOut], {
        stdio: 'ignore',
        timeout: 180000,
        env: { ...process.env, SPHERE_LICENSE_REQUIRED: 'false' },
      });
      for (const f of [warmIn, warmOut, warmOut + '.sphere.json']) fs.rmSync(f, { force: true });
      log('binary warmed ✓ — first run will be fast.');
    } catch (_) { /* best-effort; user just pays the scan on first run */ }
  }

  log(`installed sealed binary for ${KEY} ✓`);
} catch (e) {
  fail(`failed to install binary: ${e.message}\n` +
    `You can retry with: npm rebuild sphere-cli`);
}
