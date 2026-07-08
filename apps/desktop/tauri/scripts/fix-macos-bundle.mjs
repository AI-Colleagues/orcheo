import { spawnSync } from 'node:child_process'
import { cpSync, existsSync, readdirSync, rmSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// Tauri's declarative `bundle.resources` copying (tauri-utils' ResourcePaths,
// used by tauri-bundler's `copy_resources`) dereferences symlinks via
// `Path::is_dir()`/`fs::copy`, which silently drops any symlink pointing to a
// directory and duplicates any symlink pointing to a file. macOS's standard
// versioned-framework layout - which Chromium's "Chrome for Testing" bundle
// uses - relies on exactly those symlinks (Framework.framework/{Resources,
// Libraries,Helpers} -> Versions/Current/..., Versions/Current -> <version>).
// Routed through `bundle.resources`, this silently drops Resources/Libraries/
// Helpers from the packaged app (breaking Chromium at runtime) while
// duplicating the ~220MB framework binary. We exclude `ms-playwright` from
// `bundle.resources` in tauri.conf.json and copy it into the built .app
// ourselves here, preserving symlinks, then re-sign (this ad-hoc signature
// doesn't seal Resources, so replacing them is safe, matching what
// scripts/build-macos-app.sh already does for the native macOS build).

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const tauriDirectory = path.resolve(__dirname, '..')
const stagedPlaywright = path.join(tauriDirectory, 'bundle', 'ms-playwright')

if (process.platform !== 'darwin') {
  process.exit(0)
}

if (!existsSync(stagedPlaywright)) {
  console.log('No staged Playwright browsers found; skipping macOS bundle fix-up.')
  process.exit(0)
}

const macosBundleDir = path.join(
  tauriDirectory,
  'src-tauri',
  'target',
  'release',
  'bundle',
  'macos',
)

if (!existsSync(macosBundleDir)) {
  console.log('No macOS app bundle found; skipping fix-up.')
  process.exit(0)
}

function run(command, args) {
  const result = spawnSync(command, args, { stdio: 'inherit' })
  if (result.error) {
    throw result.error
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1)
  }
}

const appNames = readdirSync(macosBundleDir).filter((entry) => entry.endsWith('.app'))

for (const appName of appNames) {
  const appPath = path.join(macosBundleDir, appName)
  const resourceDir = path.join(appPath, 'Contents', 'Resources', 'ms-playwright')

  rmSync(resourceDir, { force: true, recursive: true })
  cpSync(stagedPlaywright, resourceDir, { dereference: false, recursive: true })
  console.log(`Restored symlink-preserving Playwright browsers in ${appName}`)

  run('codesign', ['--force', '--deep', '--sign', '-', appPath])
  console.log(`Re-signed ${appName} after fixing bundled resources`)
}

const dmgDir = path.join(tauriDirectory, 'src-tauri', 'target', 'release', 'bundle', 'dmg')

if (existsSync(dmgDir) && appNames.length > 0) {
  const dmgFiles = readdirSync(dmgDir).filter((entry) => entry.endsWith('.dmg'))
  const appPath = path.join(macosBundleDir, appNames[0])

  for (const dmgFile of dmgFiles) {
    const dmgPath = path.join(dmgDir, dmgFile)
    rmSync(dmgPath, { force: true })
    // Tauri's own styled dmg (custom icon/background/window layout) was
    // built from the broken .app and went stale the moment we fixed it
    // above, so rebuild a plain one from the corrected .app instead.
    run('hdiutil', [
      'create',
      '-volname',
      path.basename(appNames[0], '.app'),
      '-srcfolder',
      appPath,
      '-ov',
      '-format',
      'UDZO',
      dmgPath,
    ])
    console.log(`Rebuilt ${dmgFile} from the fixed .app`)
  }
}
