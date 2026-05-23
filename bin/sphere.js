#!/usr/bin/env node
'use strict';

/**
 * sphere — CLI entry point.
 *
 * This wrapper handles argument parsing, progress display, and license checks,
 * then delegates generation to sphere-node.jsc (V8 bytecode, bytenode-loaded).
 *
 * Commands:
 *   sphere generate  <input.csv>  -o <output.csv>  [options]
 *   sphere license   activate|status|clear
 *   sphere demo
 *   sphere evaluate  — not available in this version (requires Python sidecar)
 *   sphere certify   — not available in this version (requires Python sidecar)
 */

const { spawn } = require('child_process');
const path   = require('path');
const fs     = require('fs');
const https  = require('https');
const os     = require('os');

const PKG_DIR        = path.join(__dirname, '..');
const SPHERE_NODE_JS = path.join(PKG_DIR, 'sphere-node.js');
const VERSION        = require(path.join(PKG_DIR, 'package.json')).version;

// ── License ───────────────────────────────────────────────────────────────────

const LICENSE_WORKER_URL = process.env.SPHERE_WORKER_URL
  || 'https://sphere-license.statzihuai.workers.dev';
const LICENSE_CACHE_DAYS = 1;
const LICENSE_REQUIRED   = (process.env.SPHERE_LICENSE_REQUIRED || 'true') !== 'false';

function sphereConfigDir() {
  const d = path.join(os.homedir(), '.config', 'sphere');
  fs.mkdirSync(d, { recursive: true });
  return d;
}
function licenseKeyFile()   { return path.join(sphereConfigDir(), 'license_key'); }
function licenseCacheFile() { return path.join(sphereConfigDir(), 'license_cache.json'); }

function readStoredLicenseKey() {
  const f = licenseKeyFile();
  if (!fs.existsSync(f)) return null;
  return fs.readFileSync(f, 'utf8').trim() || null;
}

function writeLicenseKey(key) {
  const f = licenseKeyFile();
  fs.writeFileSync(f, key, { encoding: 'utf8', mode: 0o600 });
}

function readLicenseCache() {
  const f = licenseCacheFile();
  if (!fs.existsSync(f)) return null;
  try {
    const data = JSON.parse(fs.readFileSync(f, 'utf8'));
    const ageDays = (Date.now() / 1000 - (data.cachedAt || 0)) / 86400;
    return ageDays <= LICENSE_CACHE_DAYS ? data : null;
  } catch { return null; }
}

function writeLicenseCache(data) {
  fs.writeFileSync(
    licenseCacheFile(),
    JSON.stringify({ ...data, cachedAt: Math.floor(Date.now() / 1000) }),
    'utf8',
  );
}

function validateKeyOnline(key) {
  return new Promise((resolve, reject) => {
    const body = Buffer.from(JSON.stringify({ key }));
    const req = https.request(
      `${LICENSE_WORKER_URL}/validate`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': body.length,
          'User-Agent': 'sphere-cli/1.0',
        },
      },
      (res) => {
        let buf = '';
        res.on('data', (d) => (buf += d));
        res.on('end', () => {
          try { resolve(JSON.parse(buf)); }
          catch { reject(new Error('Invalid JSON from license server')); }
        });
      },
    );
    req.on('error', reject);
    req.setTimeout(10000, () => { req.destroy(new Error('License server timeout')); });
    req.write(body);
    req.end();
  });
}

async function checkLicense() {
  if (!LICENSE_REQUIRED) return;
  const key = readStoredLicenseKey();
  if (!key) {
    process.stderr.write(
      '✗  No SPHERE license found.\n' +
      '   Run:  sphere license activate <key>\n' +
      '   Contact zihuai@stanford.edu to get a license.\n',
    );
    process.exit(1);
  }

  let result;
  try {
    result = await validateKeyOnline(key);
    writeLicenseCache(result);
  } catch {
    result = readLicenseCache();
    if (!result) {
      process.stderr.write(
        '✗  License server unreachable and local cache has expired.\n' +
        '   Connect to the internet and re-run to refresh your license.\n',
      );
      process.exit(1);
    }
  }

  if (!result.valid) {
    process.stderr.write(
      `✗  License invalid: ${result.error || 'unknown error'}\n` +
      '   Run:  sphere license activate <key>\n',
    );
    process.exit(1);
  }
}

// ── Progress bar ──────────────────────────────────────────────────────────────

const BAR_WIDTH = 28;

function progressBar(frac, msg) {
  const f = Math.min(1, Math.max(0, frac));
  const filled = Math.round(BAR_WIDTH * f);
  const bar = '█'.repeat(filled) + '░'.repeat(BAR_WIDTH - filled);
  const pct = (f * 100).toFixed(1).padStart(5);
  process.stderr.write(`\r  [${bar}] ${pct}%  ${(msg || '').slice(0, 45).padEnd(45)}\x1b[K`);
}

function clearLine() {
  process.stderr.write(`\r${' '.repeat(BAR_WIDTH + 60)}\r`);
}

// ── Argument parsing helpers ──────────────────────────────────────────────────

function parseArgs(argv) {
  const args = argv.slice(2);
  const pos = [];
  const flags = {};
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '-o') {
      flags['output'] = args[++i];
    } else if (a.startsWith('--')) {
      const key = a.slice(2);
      const next = args[i + 1];
      if (next === undefined || next.startsWith('-')) {
        flags[key] = true;
      } else {
        flags[key] = next;
        i++;
      }
    } else if (a.startsWith('-') && a.length === 2) {
      flags[a.slice(1)] = args[++i];
    } else {
      pos.push(a);
    }
  }
  return { pos, flags };
}

// ── sphere generate ───────────────────────────────────────────────────────────

async function cmdGenerate(pos, flags) {
  await checkLicense();

  const input  = pos[0];
  const output = flags.output;

  if (!input)  { process.stderr.write('Error: missing input CSV\n'); process.exit(1); }
  if (!output) { process.stderr.write('Error: missing -o / --output path\n'); process.exit(1); }
  if (!fs.existsSync(input)) {
    process.stderr.write(`Error: file not found: ${input}\n`);
    process.exit(1);
  }

  if (!flags.json) {
    process.stderr.write(`Generating synthetic data from ${path.basename(input)} …\n`);
  }

  // Build args for sphere-node.js
  const nodeArgs = [SPHERE_NODE_JS, input, output];
  if (flags.k)           nodeArgs.push('--k',        String(flags.k));
  if (flags.theta)       nodeArgs.push('--theta',     String(flags.theta));
  if (flags.delta)       nodeArgs.push('--delta',     String(flags.delta));
  if (flags['mix-prob']) nodeArgs.push('--mix-prob',  String(flags['mix-prob']));
  if (flags.seed)        nodeArgs.push('--seed',      String(flags.seed));

  return new Promise((resolve) => {
    const proc = spawn(process.execPath, nodeArgs, { stdio: ['ignore', 'pipe', 'pipe'] });

    let resultLine = '';
    let errorLine  = '';

    proc.stdout.on('data', (d) => { resultLine += d.toString(); });

    proc.stderr.on('data', (d) => {
      for (const line of d.toString().split('\n')) {
        const s = line.trim();
        if (!s) continue;
        try {
          const msg = JSON.parse(s);
          if (typeof msg.progress === 'number' && !flags.json) {
            progressBar(msg.progress, '');
          } else if (msg.error) {
            errorLine = msg.error;
          }
        } catch { /* ignore non-JSON lines */ }
      }
    });

    proc.on('close', (code) => {
      if (!flags.json) clearLine();

      if (code !== 0) {
        process.stderr.write(`Error: ${errorLine || 'sphere-node exited with code ' + code}\n`);
        process.exit(1);
      }

      let result;
      try { result = JSON.parse(resultLine.trim()); }
      catch {
        process.stderr.write('Error: could not parse sphere-node output\n');
        process.exit(1);
      }

      if (flags.json) {
        process.stdout.write(JSON.stringify(result) + '\n');
        resolve();
        return;
      }

      const elapsedS = ((result.elapsedMs || 0) / 1000).toFixed(1);
      process.stdout.write(
        `✓ ${output}  ${(result.rows || 0).toLocaleString()} rows \xd7 ${result.cols || '?'} cols` +
        `  (${elapsedS} s)  seed ${result.seed}\n`,
      );
      if (result.idColDetected) {
        process.stdout.write(`  ID columns excluded: ${result.idColName}\n`);
      }
      resolve();
    });

    proc.on('error', (err) => {
      process.stderr.write(`Error: failed to start sphere-node: ${err.message}\n`);
      process.exit(1);
    });
  });
}

// ── sphere demo ───────────────────────────────────────────────────────────────

async function cmdDemo() {
  const exampleCsv = path.join(PKG_DIR, 'examples', 'nhanes_sample.csv');
  if (!fs.existsSync(exampleCsv)) {
    process.stderr.write('Error: built-in example not found at ' + exampleCsv + '\n');
    process.exit(1);
  }

  const { randomUUID } = require('crypto');
  const tmpOut = path.join(os.tmpdir(), `sphere-demo-${randomUUID()}.csv`);

  process.stdout.write('SPHERE demo — built-in NHANES dataset (4,899 rows \xd7 18 cols)\n');
  process.stdout.write('─'.repeat(52) + '\n\n');

  try {
    await cmdGenerate([exampleCsv], { output: tmpOut });
  } finally {
    try { fs.unlinkSync(tmpOut); } catch {}
    try { fs.unlinkSync(tmpOut + '.sphere.json'); } catch {}
  }

  process.stdout.write('\nTry it on your own data:\n');
  process.stdout.write('  sphere generate your_data.csv -o synthetic.csv\n\n');
}

// ── sphere license ────────────────────────────────────────────────────────────

async function cmdLicense(sub, pos, flags) {
  if (sub === 'activate') {
    let key = (pos[0] || flags.key || '').trim();
    if (!key) {
      // Simple stdin prompt (no readline dependency)
      process.stdout.write('SPHERE license key (sphere_…): ');
      key = await new Promise((resolve) => {
        let buf = '';
        process.stdin.resume();
        process.stdin.setEncoding('utf8');
        process.stdin.on('data', (ch) => {
          const c = ch.toString();
          if (c.includes('\n') || c.includes('\r')) {
            process.stdin.pause();
            process.stdout.write('\n');
            resolve(buf.trim());
          } else {
            buf += c;
          }
        });
      });
    }
    if (!key.startsWith('sphere_')) {
      process.stderr.write("Error: key must start with 'sphere_'\n");
      process.exit(1);
    }
    process.stdout.write('Validating …');
    let result;
    try { result = await validateKeyOnline(key); }
    catch (e) {
      process.stderr.write(`\nError: could not reach license server — ${e.message}\n`);
      process.exit(1);
    }
    if (!result.valid) {
      process.stderr.write(`\r✗ Invalid key: ${result.error || 'unknown error'}\n`);
      process.exit(1);
    }
    writeLicenseKey(key);
    writeLicenseCache(result);
    process.stdout.write(`\r✓ License activated  —  ${result.customer || ''}\n`);
    if (result.expiry) process.stdout.write(`  Expires: ${result.expiry}\n`);
    return;
  }

  if (sub === 'status') {
    const key = readStoredLicenseKey();
    if (!key) {
      process.stdout.write('✗ No license key configured.\n  Run:  sphere license activate <key>\n');
      return;
    }
    process.stdout.write('Checking …');
    let offline = false;
    let result;
    try {
      result = await validateKeyOnline(key);
      writeLicenseCache(result);
    } catch {
      result = readLicenseCache();
      offline = true;
      if (!result) {
        process.stdout.write('\r✗ License server unreachable and cache expired.\n');
        return;
      }
    }
    if (result.valid) {
      const suffix = offline ? '  (offline — cached)' : '';
      process.stdout.write(`\r✓ License valid  —  ${result.customer || ''}${suffix}\n`);
      if (result.expiry) process.stdout.write(`  Expires: ${result.expiry}\n`);
    } else {
      process.stdout.write(`\r✗ License invalid: ${result.error || 'unknown'}\n`);
    }
    return;
  }

  if (sub === 'clear') {
    let removed = false;
    for (const f of [licenseKeyFile(), licenseCacheFile()]) {
      if (fs.existsSync(f)) { fs.unlinkSync(f); removed = true; }
    }
    process.stdout.write(removed ? '✓ License cleared.\n' : 'No license stored.\n');
    return;
  }

  process.stderr.write('Usage: sphere license activate|status|clear\n');
  process.exit(1);
}

// ── Main dispatch ─────────────────────────────────────────────────────────────

async function main() {
  const { pos, flags } = parseArgs(process.argv);
  const cmd = pos[0];

  if (flags.version || flags.v) {
    process.stdout.write(`${VERSION}\n`);
    process.exit(0);
  }

  if (!cmd || flags.help || flags.h) {
    process.stdout.write(
      `SPHERE CLI v${VERSION} — synthetic data generation\n\n` +
      'Usage:\n' +
      '  sphere generate <input.csv> -o <output.csv> [options]\n' +
      '  sphere license  activate|status|clear\n' +
      '  sphere demo\n\n' +
      'Generate options:\n' +
      '  --k <int>          Synthesis depth (default: 2)\n' +
      '  --theta <float>    Rotation angle in radians (default: π/6 ≈ 0.524)\n' +
      '  --delta <float>    Per-pair angle jitter (default: 5° ≈ 0.087)\n' +
      '  --mix-prob <float> Privacy/utility trade-off (default: 0.75)\n' +
      '  --seed <int>       Integer seed for reproducible output\n' +
      '  --json             Machine-readable JSON output\n',
    );
    process.exit(0);
  }

  if (cmd === 'generate') {
    await cmdGenerate(pos.slice(1), flags);
    return;
  }

  if (cmd === 'demo') {
    await cmdDemo();
    return;
  }

  if (cmd === 'license') {
    await cmdLicense(pos[1], pos.slice(2), flags);
    return;
  }

  if (cmd === 'evaluate' || cmd === 'certify') {
    process.stderr.write(
      `'sphere ${cmd}' requires the Python evaluation engine, which is not included\n` +
      'in this version. Coming soon.\n',
    );
    process.exit(1);
  }

  process.stderr.write(`Unknown command: ${cmd}\nRun 'sphere --help' for usage.\n`);
  process.exit(1);
}

main().catch((err) => {
  process.stderr.write(`\nError: ${err.message || err}\n`);
  process.exit(1);
});
