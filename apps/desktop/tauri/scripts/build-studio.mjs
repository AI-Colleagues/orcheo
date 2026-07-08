import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const studioDirectory = path.resolve(__dirname, '../../../studio')

const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm'
const result = spawnSync(npmCommand, ['run', 'build'], {
  cwd: studioDirectory,
  env: {
    ...process.env,
    VITE_ORCHEO_AUTH_DISABLED: process.env.VITE_ORCHEO_AUTH_DISABLED ?? 'true',
  },
  stdio: 'inherit',
})

if (result.error) {
  throw result.error
}

process.exit(result.status ?? 1)
