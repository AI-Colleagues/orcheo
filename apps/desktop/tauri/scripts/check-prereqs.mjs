import { spawnSync } from 'node:child_process'

const checks = [
  {
    command: 'cargo',
    args: ['--version'],
    installHint:
      'Install Rust/Cargo with rustup: https://rustup.rs/ . Then restart your shell so cargo is on PATH.',
  },
  {
    command: 'rustc',
    args: ['--version'],
    installHint:
      'Install Rust/Cargo with rustup: https://rustup.rs/ . Then restart your shell so rustc is on PATH.',
  },
  {
    command: 'uv',
    args: ['--version'],
    installHint:
      'Install uv from https://docs.astral.sh/uv/getting-started/installation/ . The Tauri build uses it to install Python dependencies and Playwright Chromium.',
  },
]

const missing = []

for (const check of checks) {
  const result = spawnSync(check.command, check.args, {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  })

  if (result.error || result.status !== 0) {
    missing.push(check)
  }
}

if (missing.length > 0) {
  console.error('Missing Tauri desktop prerequisite(s):')
  for (const check of missing) {
    console.error(`- ${check.command}: ${check.installHint}`)
  }
  console.error('')
  console.error('The Studio chunk-size message is a Vite warning, not the build failure.')
  process.exit(1)
}

console.log('Tauri desktop prerequisites found.')
