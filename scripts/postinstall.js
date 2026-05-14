'use strict';

/**
 * Downloads the correct platform-specific SPHERE CLI binary from GitHub
 * Releases and extracts it to native/ inside the npm package directory.
 *
 * Tarball structure (built by CI):
 *   sphere-cli/        ← PyInstaller onedir bundle
 *     sphere           ← the executable
 *     _internal/       ← Python runtime + deps
 *   install.sh
 *
 * After extraction, bin/sphere.js invokes native/sphere-cli/sphere.
 */

const https   = require('https');
const fs      = require('fs');
const path    = require('path');
const os      = require('os');
const { execFileSync } = require('child_process');

const REPO    = 'statzihuai/sphere-cli';
const VERSION = require('../package.json').version;
const PKG_DIR = path.join(__dirname, '..');

// ── Map Node.js platform/arch to our release tarball names ───────────────────
function getPlatformKey() {
  const p = process.platform;
  const a = process.arch;
  if (p === 'darwin' && a === 'arm64') return 'macos-arm64';
  if (p === 'linux'  && a === 'x64')   return 'linux-x86_64';
  if (p === 'linux'  && a === 'arm64') return 'linux-arm64';
  throw new Error(
    `Unsupported platform: ${p}-${a}\n` +
    'Supported: macOS arm64, Linux x86_64, Linux arm64.\n' +
    'Use the curl installer instead: https://github.com/statzihuai/sphere-cli#install'
  );
}

// ── HTTPS GET with redirect following ────────────────────────────────────────
function download(url, destPath) {
  return new Promise((resolve, reject) => {
    function get(url, redirects) {
      if (redirects > 5) { reject(new Error('Too many redirects')); return; }
      https.get(url, { headers: { 'User-Agent': 'sphere-cli-npm-installer' } }, res => {
        if (res.statusCode === 301 || res.statusCode === 302) {
          get(res.headers.location, redirects + 1);
          return;
        }
        if (res.statusCode !== 200) {
          reject(new Error(`HTTP ${res.statusCode} downloading ${url}`));
          res.resume();
          return;
        }
        const out = fs.createWriteStream(destPath);
        res.pipe(out);
        out.on('finish', () => out.close(resolve));
        out.on('error', reject);
        res.on('error', reject);
      }).on('error', reject);
    }
    get(url, 0);
  });
}

// ── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  // Skip in CI environments where binaries are built fresh
  if (process.env.SPHERE_SKIP_POSTINSTALL === '1') {
    console.log('SPHERE_SKIP_POSTINSTALL=1 — skipping binary download.');
    return;
  }

  const platform = getPlatformKey();
  const tarball  = `sphere-cli-${platform}.tar.gz`;
  const url      = `https://github.com/${REPO}/releases/download/v${VERSION}/${tarball}`;
  const nativeDir = path.join(PKG_DIR, 'native');
  const tmpTar    = path.join(os.tmpdir(), `sphere-cli-install-${process.pid}.tar.gz`);

  // Re-download whenever the installed version doesn't match the package version.
  // This ensures `npm install -g sphere-cli` or `npm update -g sphere-cli` always
  // delivers the correct binary even when one is already present from a prior release.
  const binaryPath  = path.join(nativeDir, 'sphere-cli', 'sphere');
  const markerPath  = path.join(nativeDir, '.sphere-cli-version');
  const installedVer = fs.existsSync(markerPath)
    ? fs.readFileSync(markerPath, 'utf8').trim()
    : null;

  if (fs.existsSync(binaryPath) && installedVer === VERSION) {
    console.log(`✓ SPHERE CLI v${VERSION} already installed — skipping download.`);
    return;
  }

  if (installedVer && installedVer !== VERSION) {
    console.log(`\nUpgrading SPHERE CLI ${installedVer} → ${VERSION} for ${platform} …`);
  } else {
    console.log(`\nDownloading SPHERE CLI v${VERSION} for ${platform} …`);
  }
  console.log(`  ${url}\n`);

  try {
    await download(url, tmpTar);
  } catch (err) {
    throw new Error(
      `Failed to download SPHERE CLI binary: ${err.message}\n\n` +
      'Alternatives:\n' +
      `  • curl installer: curl -fsSL https://github.com/${REPO}/releases/latest/download/install.sh | sh\n` +
      `  • Manual download: ${url}`
    );
  }

  fs.mkdirSync(nativeDir, { recursive: true });

  try {
    execFileSync('tar', ['-xzf', tmpTar, '-C', nativeDir]);
  } finally {
    try { fs.unlinkSync(tmpTar); } catch {}
  }

  // Ensure the binary is executable
  fs.chmodSync(binaryPath, 0o755);

  // Write version marker so future postinstall runs can detect upgrades
  fs.writeFileSync(markerPath, VERSION, 'utf8');

  console.log('✓ SPHERE CLI installed.\n');
  console.log('  Quick start:');
  console.log('    sphere generate data.csv -o synth.csv');
  console.log('    sphere evaluate real.csv synth.csv');
  console.log('    sphere certify  real.csv synth.csv -o report.html');
  console.log('');
  console.log("  Run 'sphere --help' for full options.\n");
}

main().catch(err => {
  console.error('\n✗ SPHERE CLI postinstall failed:\n  ' + err.message);
  console.error('\nThe npm package was installed but the binary was not downloaded.');
  console.error('Running `sphere` will show an error until you reinstall.\n');
  // Exit 0 so npm install doesn't fail — user can fix later
  process.exit(0);
});
