#!/usr/bin/env node
'use strict';

/**
 * sphere — thin launcher.
 *
 * The actual CLI is a sealed, signed+notarized native binary (PyInstaller bundle
 * of the SPHERE engine — same engine as the desktop app). The npm `postinstall`
 * downloads the platform-specific binary into ./vendor/. This launcher just
 * forwards argv to it. No readable algorithm code ships in the npm package.
 */

const path = require('path');
const fs = require('fs');
const { spawnSync } = require('child_process');

const BIN = path.join(__dirname, '..', 'vendor', 'sphere-cli', 'sphere');

if (!fs.existsSync(BIN)) {
  process.stderr.write(
    '\nSPHERE binary not found.\n' +
    'The platform binary is downloaded by the install step. Try reinstalling:\n' +
    '  npm install -g sphere-cli\n' +
    'If your platform is unsupported, see https://github.com/statzihuai/sphere-cli\n',
  );
  process.exit(1);
}

const res = spawnSync(BIN, process.argv.slice(2), { stdio: 'inherit' });
if (res.error) {
  process.stderr.write(`Failed to launch sphere: ${res.error.message}\n`);
  process.exit(1);
}
process.exit(res.status === null ? 1 : res.status);
