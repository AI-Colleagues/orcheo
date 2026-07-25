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
1. **Update version**: Edit the target package's metadata and bump its `version`
   field. Keep package versions independent. You can automate the
   edit with `bump2version` using the package-specific configuration files:

   ```bash
   # examples
   uv run bump2version --new-version 0.45.0a1 patch
   (cd apps/backend && uv run bump2version --new-version 0.39.0b1 patch)
   (cd packages/sdk && uv run bump2version --new-version 0.34.0rc1 patch)
   (cd apps/studio && uv run bump2version --new-version 0.25.0-alpha.1 patch)
   (cd deploy/stack && uv run bump2version --new-version 0.29.0-alpha.1 patch)
   ```

   The configurations understand stable, alpha, beta, and release-candidate
   versions. They update files only; create the commit through the normal pull
   request flow and tag the merged commit manually.
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
5. **Commit** the changes and open a pull request. Merge once CI is green.
6. **Tag the release** from the merged commit using the naming convention in the table
   below, then push the tag.

| Package | Stable tag | Prerelease example |
| --- | --- | --- |
| `orcheo` | `core-vX.Y.Z` | `core-vX.Y.Z-alpha.1` |
| `orcheo-backend` | `backend-vX.Y.Z` | `backend-vX.Y.Z-beta.1` |
| `orcheo-sdk` | `sdk-vX.Y.Z` | `sdk-vX.Y.Z-rc.1` |
| `agentensor` | `agentensor-vX.Y.Z` | `agentensor-vX.Y.Z-alpha.1` |
| `orcheo-studio` | `studio-vX.Y.Z` | `studio-vX.Y.Z-beta.1` |
| stack images | `stack-vX.Y.Z` | `stack-vX.Y.Z-rc.1` |
| desktop apps | `desktop-vX.Y.Z` | `desktop-vX.Y.Z-rc.1` |

Python metadata uses canonical PEP 440 suffixes (`a1`, `b1`, and `rc1`).
Git, GitHub, npm, Docker, and desktop tags use the SemVer spellings shown in
the table. CI normalizes Python versions before comparing package metadata with
the tag.

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

Stack prereleases resolve the newest public version of each first-party Python
package, selecting a prerelease when it is newer than the stable package.
Stable stacks resolve only stable first-party packages. Studio uses the npm
`prerelease` channel for prerelease stacks and `latest` for stable stacks.

## Package-specific Notes
### orcheo (core)
1. Run `uv run bump2version <part>` from the repository root, or provide an
   exact prerelease with `--new-version`.
2. If new public APIs were added, update `README.md` and relevant docs.
3. Push the release commit and tag: `git push origin HEAD && git push origin core-vX.Y.Z`.

### orcheo-backend
1. Ensure `apps/backend/pyproject.toml` references the desired `orcheo` version in
   its dependencies.
2. Run `(cd apps/backend && uv run bump2version <part>)` to update the version.
3. Push the release commit and tag: `git push origin HEAD && git push origin backend-vX.Y.Z`.

### orcheo-sdk
1. Run `(cd packages/sdk && uv run bump2version <part>)` to update the version.
2. Update SDK documentation or examples if interfaces changed.
3. Push the release commit and tag: `git push origin HEAD && git push origin sdk-vX.Y.Z`.

### stack image
1. Run `(cd deploy/stack && uv run bump2version <part>)` to update the stack version.
2. Ensure `deploy/stack/` contains the intended compose and widget assets.
3. Push the release commit and tag: `git push origin HEAD && git push origin stack-vX.Y.Z`.

### desktop apps
1. Run `(cd apps/desktop && uv run bump2version <part>)` to update both the
   native macOS and Tauri app versions.
2. Increment the macOS bundle build number independently for each packaged
   build, keeping `ORCHEO_MACOS_BUILD` and Tauri's
   `bundle.macOS.bundleVersion` aligned.
3. Push the release commit and tag: `git push origin HEAD && git push origin desktop-vX.Y.Z`.

## Post-release Follow-up
- Announce the release, update sample code, and communicate dependency expectations
  (e.g., minimum `orcheo` version required by `orcheo-backend`).
- Remove local `dist/` directories if you performed a manual build.
