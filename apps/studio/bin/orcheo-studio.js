#!/usr/bin/env node

import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const projectRoot = join(__dirname, '..');

const args = process.argv.slice(2);
const command = args[0] || 'preview';

const scripts = {
  dev: ['npm', ['run', 'dev']],
  build: ['npm', ['run', 'build']],
  preview: ['npm', ['run', 'preview']],
  start: ['npm', ['run', 'preview']], // Alias for preview
};

if (!scripts[command]) {
  console.error(`Unknown command: ${command}`);
  console.error('Available commands: dev, build, preview, start');
  process.exit(1);
}

const [cmd, cmdArgs] = scripts[command];

const shouldUseShell = process.platform === 'win32';

const child = spawn(cmd, cmdArgs, {
  cwd: projectRoot,
  stdio: 'inherit',
  shell: shouldUseShell,
});

child.on('exit', (code) => {
  process.exit(code || 0);
});
