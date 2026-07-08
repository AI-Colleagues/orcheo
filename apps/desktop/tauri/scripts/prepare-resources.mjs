import { spawnSync } from 'node:child_process'
import {
  chmodSync,
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readlinkSync,
  rmSync,
  statSync,
  unlinkSync,
  readdirSync,
} from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const tauriDirectory = path.resolve(__dirname, '..')
const repoRoot = path.resolve(tauriDirectory, '../../..')
const bundleRoot = path.join(tauriDirectory, 'bundle')
const stagedRepo = path.join(bundleRoot, 'orcheo')
const stagedPostgres = path.join(bundleRoot, 'postgres')
const stagedPlaywright = path.join(bundleRoot, 'ms-playwright')

const excludedNames = new Set([
  '.cache',
  '.coverage-shards',
  '.git',
  '.mypy_cache',
  '.orcheo',
  '.pytest_cache',
  '.ruff_cache',
  '.tox',
  '.venv',
  '__pycache__',
  'build',
  'dist-ssr',
  'htmlcov',
  'node_modules',
  'target',
])

// The backend only needs the workspace members it actually depends on
// (`uv run` from the repo root installs this whole workspace) plus the one
// script the desktop shell shells out to. Everything else in the monorepo
// (other apps, docs, marketing sites, test fixtures, agent skills, etc.) is
// dead weight in a packaged app and was previously copied wholesale.
const requiredRootFiles = ['pyproject.toml', 'uv.lock', 'README.md', '.python-version']
const requiredRootDirs = ['src', 'apps/backend', 'packages/agentensor', 'packages/sdk']
const requiredScriptFiles = ['scripts/desktop-postgres.sh']

function shouldCopy(sourcePath) {
  const name = path.basename(sourcePath)

  if (excludedNames.has(name)) {
    return false
  }

  if (name === '.DS_Store' || name.endsWith('.pyc') || name.endsWith('.pyo')) {
    return false
  }

  return true
}

function copyFilteredDirectory(sourceDirectory, destinationDirectory) {
  mkdirSync(destinationDirectory, { recursive: true })

  for (const entry of readdirSync(sourceDirectory)) {
    const sourcePath = path.join(sourceDirectory, entry)
    if (!shouldCopy(sourcePath)) {
      continue
    }

    const destinationPath = path.join(destinationDirectory, entry)
    const stat = lstatSync(sourcePath)
    if (stat.isSymbolicLink()) {
      continue
    } else if (stat.isDirectory()) {
      copyFilteredDirectory(sourcePath, destinationPath)
    } else if (stat.isFile()) {
      cpSync(sourcePath, destinationPath, {
        dereference: false,
        force: true,
        preserveTimestamps: true,
      })
    }
  }
}

function normalizeResourcePermissions(resourceDirectory) {
  if (!existsSync(resourceDirectory)) {
    return
  }

  for (const entry of readdirSync(resourceDirectory)) {
    const entryPath = path.join(resourceDirectory, entry)
    const stat = lstatSync(entryPath)
    if (stat.isSymbolicLink()) {
      const targetPath = path.resolve(path.dirname(entryPath), readlinkSync(entryPath))
      const targetStat = statSync(targetPath)
      unlinkSync(entryPath)
      cpSync(targetPath, entryPath, {
        dereference: true,
        recursive: targetStat.isDirectory(),
      })
      if (targetStat.isDirectory()) {
        chmodSync(entryPath, 0o755)
        normalizeResourcePermissions(entryPath)
      } else {
        const executable = (targetStat.mode & 0o111) !== 0
        chmodSync(entryPath, executable ? 0o755 : 0o644)
      }
      continue
    }
    if (stat.isDirectory()) {
      chmodSync(entryPath, 0o755)
      normalizeResourcePermissions(entryPath)
    } else if (stat.isFile()) {
      const executable = (stat.mode & 0o111) !== 0
      chmodSync(entryPath, executable ? 0o755 : 0o644)
    }
  }
}

function prunePostgresDevelopmentFiles(postgresDirectory) {
  rmSync(path.join(postgresDirectory, 'include'), { force: true, recursive: true })
  rmSync(path.join(postgresDirectory, 'lib', 'postgresql', 'pkgconfig'), {
    force: true,
    recursive: true,
  })

  const postgresLibDirectory = path.join(postgresDirectory, 'lib', 'postgresql')
  if (!existsSync(postgresLibDirectory)) {
    return
  }
  for (const entry of readdirSync(postgresLibDirectory)) {
    if (entry.endsWith('.a')) {
      rmSync(path.join(postgresLibDirectory, entry), { force: true })
    }
  }
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: repoRoot,
    env: process.env,
    stdio: 'inherit',
    ...options,
  })

  if (result.error) {
    throw result.error
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1)
  }
}

rmSync(stagedRepo, { force: true, recursive: true })
rmSync(stagedPostgres, { force: true, recursive: true })
rmSync(stagedPlaywright, { force: true, recursive: true })
mkdirSync(stagedRepo, { recursive: true })
mkdirSync(stagedPostgres, { recursive: true })
mkdirSync(stagedPlaywright, { recursive: true })

if (!existsSync(path.join(repoRoot, 'apps/studio/dist/index.html'))) {
  console.error(
    `Studio is not built at ${path.join(repoRoot, 'apps/studio/dist')}. Run `
      + '`npm --prefix apps/studio run build` first.',
  )
  process.exit(1)
}

for (const relativePath of requiredRootFiles) {
  const sourcePath = path.join(repoRoot, relativePath)
  if (existsSync(sourcePath)) {
    cpSync(sourcePath, path.join(stagedRepo, relativePath), {
      force: true,
      preserveTimestamps: true,
    })
  }
}

for (const relativePath of requiredRootDirs) {
  copyFilteredDirectory(path.join(repoRoot, relativePath), path.join(stagedRepo, relativePath))
}

for (const relativePath of requiredScriptFiles) {
  const destinationPath = path.join(stagedRepo, relativePath)
  mkdirSync(path.dirname(destinationPath), { recursive: true })
  cpSync(path.join(repoRoot, relativePath), destinationPath, {
    force: true,
    preserveTimestamps: true,
  })
}

if (process.platform === 'darwin' && process.env.ORCHEO_TAURI_BUNDLE_POSTGRES !== 'false') {
  run('bash', ['scripts/bundle-postgres-macos.sh', stagedPostgres])
  prunePostgresDevelopmentFiles(stagedPostgres)
}

if (process.env.ORCHEO_TAURI_BUNDLE_PLAYWRIGHT !== 'false') {
  run('uv', ['run', 'python', '-m', 'playwright', 'install', 'chromium', 'chromium-headless-shell'], {
    env: {
      ...process.env,
      PLAYWRIGHT_BROWSERS_PATH: stagedPlaywright,
    },
  })
}

// Only Postgres needs this: Homebrew's binaries are symlinks pointing outside
// the staged bundle (into /opt/homebrew/Cellar/...), which must be resolved
// to real files to be portable. Playwright's own installer already ships
// correct permissions, and its browser bundles use macOS's standard versioned
// framework layout (Framework.framework/{Resources,Libraries,Helpers} as
// symlinks into Versions/Current/, itself a symlink to the version dir) —
// symlinks that point *inside* the same tree and are already portable as-is.
// Dereferencing those would duplicate the framework payload 2-3x over.
normalizeResourcePermissions(stagedPostgres)

const requiredFiles = [
  'pyproject.toml',
  'uv.lock',
  'apps/backend/src/orcheo_backend/app/__init__.py',
  'scripts/desktop-postgres.sh',
]

if (process.platform === 'darwin' && process.env.ORCHEO_TAURI_BUNDLE_POSTGRES !== 'false') {
  requiredFiles.push('../postgres/bin/initdb', '../postgres/bin/pg_ctl', '../postgres/bin/postgres')
}

if (process.env.ORCHEO_TAURI_BUNDLE_PLAYWRIGHT !== 'false') {
  requiredFiles.push('../ms-playwright')
}

const missingFiles = requiredFiles.filter((relativePath) => {
  return !existsSync(path.join(stagedRepo, relativePath))
})

if (missingFiles.length > 0) {
  console.error('Staged Tauri repo bundle is missing required files:')
  for (const file of missingFiles) {
    console.error(`- ${file}`)
  }
  process.exit(1)
}

console.log(`Prepared Tauri repo bundle at ${stagedRepo}`)
