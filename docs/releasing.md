# Releasing Orcheo Packages

This repository publishes four Python distributions independently, plus Studio,
a versioned stack container image, and the desktop app:

- `orcheo` – core orchestration engine (`core-v*` tags)
- `orcheo-sdk` – Python SDK helpers (`sdk-v*` tags)
- `orcheo-backend` – deployable FastAPI wrapper (`backend-v*` tags)
- `agentensor` – agent prompt tensors and optimizers (`agentensor-v*` tags)
- `orcheo-studio` – npm Studio package (`studio-v*` tags)
- `ghcr.io/ai-colleagues/orcheo-stack` – stack runtime image (`stack-v*` tags)
- Orcheo Desktop – native macOS and Tauri apps (`desktop-v*` tags)

The release jobs inside `.github/workflows/ci.yml` publish the matching
package, image, or desktop app whenever a tag with the corresponding prefix is
pushed. Follow the steps below to prepare and cut a release.

## Prerequisites
- `uv` installed locally, matching the version used in CI.
- Ability to push tags to the repository.
- PyPI trusted publishing configured for the repository (already set up in CI).

## Shared Release Checklist
1. **Update version**: Bump the target package's `version` with `bump2version`
   using the package-specific configuration files. Keep package versions
   independent.

   ```bash
   # examples
   uv run bump2version --new-version 0.45.0a1 patch
   (cd apps/backend && uv run bump2version --new-version 0.39.0b1 patch)
   (cd packages/sdk && uv run bump2version --new-version 0.34.0rc1 patch)
   (cd apps/studio && uv run bump2version --new-version 0.25.0-alpha.1 patch)
   (cd deploy/stack && uv run bump2version --new-version 0.29.0-alpha.1 patch)
   ```

   The configurations understand stable, alpha, beta, and release-candidate
   versions. Each run edits the version files, **commits** the change locally
   (`commit = true`), and **creates the release tag locally** (`tag = true`)
   pointing at that commit — it does not push either. Don't push the tag until
   after the PR merges (see step 6); pushing it is what triggers the CI
   release job.
2. **Update changelog/docs**: Capture the changes since the last release.
3. **Sync dependencies**: Run `uv sync --all-groups` if dependencies changed so the
   lockfile stays up to date.
4. **Verify quality gates**:
   ```bash
   uv run make lint
   uv run make test
   uv build --package <package-name>
   ```
   When the plugin ecosystem changes, also verify:
   - `orcheo plugin install "git+https://github.com/AI-Colleagues/orcheo-plugin-wecom-listener.git"`
   - `orcheo plugin install "git+https://github.com/AI-Colleagues/orcheo-plugin-lark-listener.git"`
   - successful validation of the shared Studio template
     `template-wecom-lark-shared-listener`
   - plugin edge compatibility checks remain green
5. **Open a pull request** from the branch containing the bump commit (push the
   branch — not the tag). Merge once CI is green.
6. **Push the release tag** created in step 1, using the naming convention in
   the table below. If the merge preserved the original commit hash (a merge
   or rebase merge), push the existing local tag directly:
   `git push origin <tag-name>`. If the PR was squash-merged onto a new
   commit, re-point the tag first: `git tag -f <tag-name> <merged-commit-sha>`.

| Package | Stable tag | Prerelease example |
| --- | --- | --- |
| `orcheo` | `core-vX.Y.Z` | `core-vX.Y.Za1` |
| `orcheo-backend` | `backend-vX.Y.Z` | `backend-vX.Y.Zb1` |
| `orcheo-sdk` | `sdk-vX.Y.Z` | `sdk-vX.Y.Zrc1` |
| `agentensor` | `agentensor-vX.Y.Z` | `agentensor-vX.Y.Za1` |
| `orcheo-studio` | `studio-vX.Y.Z` | `studio-vX.Y.Z-beta.1` |
| stack images | `stack-vX.Y.Z` | `stack-vX.Y.Z-rc.1` |
| desktop apps | `desktop-vX.Y.Z` | `desktop-vX.Y.Z-rc.1` |

Python metadata uses canonical PEP 440 suffixes (`a1`, `b1`, and `rc1`), and
`bump2version` tags the four Python packages (`orcheo`, `orcheo-backend`,
`orcheo-sdk`, `agentensor`) with that same spelling, e.g. `core-v0.45.0a1`.
Studio, the stack image, and desktop apps use SemVer prerelease spellings
(`-alpha.N`, `-beta.N`, `-rc.N`) for both metadata and tags. CI's tag-format
check for the Python packages accepts either spelling and normalizes with
`packaging.version.Version` before comparing package metadata with the tag.

CI automatically runs checks, then executes the release job matching the tag.
The stack release job publishes matching versioned stack and Studio images.
Stable tags also advance `latest`. Prerelease tags advance `prerelease` and
their phase tag (`alpha`, `beta`, or `rc`) without changing `latest`.

## Prerelease Progression

For a target release such as `0.45.0`, use:

```text
Python metadata: 0.45.0a1 -> 0.45.0b1 -> 0.45.0rc1 -> 0.45.0
Release tags:     0.45.0-alpha.1 -> 0.45.0-beta.1 -> 0.45.0-rc.1 -> 0.45.0
```

Never reuse a published version. Increment the phase number when replacing an
alpha, beta, or release candidate.

Stack images install the exact first-party Python and Studio versions declared
by the tagged repository revision. Stable stack tags reject prerelease package
versions; prerelease stack tags may combine stable and prerelease packages.

## Package-specific Notes
### orcheo (core)
1. Run `uv run bump2version <part>` from the repository root, or provide an
   exact prerelease with `--new-version`. This commits the bump and tags it
   `core-v<version>` locally.
2. If new public APIs were added, update `README.md` and relevant docs.
3. Push the branch, open a PR, and merge. Then push the tag:
   `git push origin core-vX.Y.Z` (re-point it to the merged commit first if
   the PR was squash-merged).

### orcheo-backend
1. Ensure `apps/backend/pyproject.toml` references the desired `orcheo` version in
   its dependencies.
2. Run `(cd apps/backend && uv run bump2version <part>)` to update the version.
   This commits the bump and tags it `backend-v<version>` locally.
3. Push the branch, open a PR, and merge. Then push the tag:
   `git push origin backend-vX.Y.Z` (re-point it to the merged commit first if
   the PR was squash-merged).

### orcheo-sdk
1. Run `(cd packages/sdk && uv run bump2version <part>)` to update the version.
   This commits the bump and tags it `sdk-v<version>` locally.
2. Update SDK documentation or examples if interfaces changed.
3. Push the branch, open a PR, and merge. Then push the tag:
   `git push origin sdk-vX.Y.Z` (re-point it to the merged commit first if the
   PR was squash-merged).

### stack image
1. Run `(cd deploy/stack && uv run bump2version <part>)` to update the stack
   version. This commits the bump and tags it `stack-v<version>` locally.
2. Ensure the Python and Studio versions declared by the tagged revision have
   already been published. Stack builds pin those exact versions so rebuilding
   the same tag remains deterministic.
3. Ensure `deploy/stack/` contains the intended compose and widget assets.
4. Push the branch, open a PR, and merge. Then push the tag:
   `git push origin stack-vX.Y.Z` (re-point it to the merged commit first if
   the PR was squash-merged).

### desktop apps
1. Run `(cd apps/desktop && uv run bump2version <part>)` to update both the
   native macOS and Tauri app versions. This commits the bump and tags it
   `desktop-v<version>` locally.
2. Increment the macOS bundle build number independently for each packaged
   build, keeping `ORCHEO_MACOS_BUILD` and Tauri's
   `bundle.macOS.bundleVersion` aligned.
3. Push the branch, open a PR, and merge. Then push the tag:
   `git push origin desktop-vX.Y.Z` (re-point it to the merged commit first if
   the PR was squash-merged).

## Post-release Follow-up
- Announce the release, update sample code, and communicate dependency expectations
  (e.g., minimum `orcheo` version required by `orcheo-backend`).
- Remove local `dist/` directories if you performed a manual build.
