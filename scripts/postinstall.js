'use strict';

/**
 * SPHERE CLI postinstall — compiles sphere-node.js to V8 bytecode.
 *
 * sphere-node.js in this package is an obfuscated JS bundle of the SPHERE
 * algorithm.  Compiling it to V8 bytecode (.jsc) at install time ensures the
 * bytecode matches the Node.js version on this machine, preventing
 * decompilation.  After compilation the source file is replaced by a tiny
 * loader stub so only bytecode remains on disk.
 *
 * This mirrors exactly how the SPHERE desktop app protects its code.
 */

const path = require('path');
const fs   = require('fs');

const PKG_DIR   = path.join(__dirname, '..');
const SRC       = path.join(PKG_DIR, 'sphere-node.js');
const OUT       = path.join(PKG_DIR, 'sphere-node.jsc');
const STUB      = "'use strict';\nrequire('bytenode');\nrequire('./sphere-node.jsc');\n";
const VERSION   = require('../package.json').version;
const MARKER    = path.join(PKG_DIR, '.sphere-node-version');

// ── Skip flag ─────────────────────────────────────────────────────────────────
if (process.env.SPHERE_SKIP_POSTINSTALL === '1') {
  process.stdout.write('SPHERE_SKIP_POSTINSTALL=1 — skipping bytecode compilation.\n');
  process.exit(0);
}

// ── Already compiled for this version? ───────────────────────────────────────
const installedVer = fs.existsSync(MARKER)
  ? fs.readFileSync(MARKER, 'utf8').trim()
  : null;

if (fs.existsSync(OUT) && installedVer === VERSION) {
  process.stdout.write(`✓ SPHERE algorithm already compiled for v${VERSION}.\n`);
  process.exit(0);
}

// ── Source check ──────────────────────────────────────────────────────────────
if (!fs.existsSync(SRC)) {
  process.stderr.write('sphere-node.js not found in package — reinstall sphere-cli.\n');
  process.exit(1);
}

const srcText = fs.readFileSync(SRC, 'utf8');
if (srcText.includes("require('bytenode')") && srcText.length < 200) {
  // Already stubbed from a previous install — .jsc must exist
  if (fs.existsSync(OUT)) {
    process.stdout.write('✓ SPHERE algorithm already compiled.\n');
    process.exit(0);
  }
  process.stderr.write('sphere-node.js is a stub but sphere-node.jsc is missing. Reinstall sphere-cli.\n');
  process.exit(1);
}

// ── Compile ───────────────────────────────────────────────────────────────────
process.stdout.write('Compiling SPHERE algorithm for your Node.js version …\n');

let bytenode;
try {
  bytenode = require('bytenode');
} catch {
  process.stderr.write(
    'bytenode is not installed — this is unexpected.\n' +
    'Try: cd "$(npm root -g)/sphere-cli" && npm install bytenode\n',
  );
  process.exit(1);
}

Promise.resolve(bytenode.compileFile({ filename: SRC, output: OUT }))
  .then(() => {
    // Replace source with loader stub so no readable code remains on disk
    fs.writeFileSync(SRC, STUB, 'utf8');
    // Write version marker
    fs.writeFileSync(MARKER, VERSION, 'utf8');

    const sizeKB = (fs.statSync(OUT).size / 1024).toFixed(1);
    process.stdout.write(`✓ SPHERE algorithm compiled (${sizeKB} KB).\n\n`);
    process.stdout.write('  Quick start:\n');
    process.stdout.write('    sphere generate data.csv -o synthetic.csv\n');
    process.stdout.write("    Run 'sphere --help' for all options.\n\n");
  })
  .catch((err) => {
    process.stderr.write(`\n✗ SPHERE compilation failed: ${err.message}\n`);
    process.stderr.write(
      'The package was installed but the algorithm could not be compiled.\n' +
      'Try reinstalling:  npm install -g sphere-cli\n\n',
    );
    // Exit 0 so npm install doesn't fail — will error on first use
    process.exit(0);
  });
