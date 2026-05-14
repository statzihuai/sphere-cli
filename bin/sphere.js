#!/usr/bin/env node
'use strict';

const { spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const binaryPath = path.join(__dirname, '..', 'native', 'sphere-cli', 'sphere');

if (!fs.existsSync(binaryPath)) {
  console.error(
    'SPHERE CLI binary not found.\n' +
    'This usually means the postinstall step failed or was skipped.\n' +
    'Try reinstalling:  npm install -g sphere-cli'
  );
  process.exit(1);
}

const result = spawnSync(binaryPath, process.argv.slice(2), {
  stdio: 'inherit',
  env: process.env,
});

process.exit(result.status ?? 1);
