import { rmSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const tauriDirectory = path.resolve(__dirname, '..')

// `src-tauri/gen/` is intentionally excluded: its schema files are committed
// to the repo (not build output), so `git status` should stay clean after
// running this script.
const targets = [
  path.join(tauriDirectory, 'bundle'),
  path.join(tauriDirectory, 'src-tauri', 'target'),
]

for (const target of targets) {
  rmSync(target, { force: true, recursive: true })
  console.log(`Removed ${target}`)
}
