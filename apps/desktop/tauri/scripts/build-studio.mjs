import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const tauriDirectory = path.resolve(__dirname, "..");
const repoRoot = path.resolve(tauriDirectory, "../../..");
const studioDirectory = path.resolve(__dirname, "../../../studio");
const releaseStudioBuildDirectory = path.join(
  tauriDirectory,
  "bundle",
  "studio-release-build",
);

function truthy(value) {
  return ["1", "true", "yes", "on"].includes(String(value ?? "").toLowerCase());
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    env: process.env,
    stdio: "inherit",
    ...options,
  });

  if (result.error) {
    throw result.error;
  }

  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

if (truthy(process.env.ORCHEO_TAURI_USE_PUBLISHED_RELEASES)) {
  const studioSpec =
    process.env.ORCHEO_TAURI_STUDIO_PACKAGE ?? "orcheo-studio@latest";
  const builtStudioDist = path.join(
    releaseStudioBuildDirectory,
    "node_modules",
    "orcheo-studio",
    "dist",
  );
  const targetStudioDist = path.join(repoRoot, "apps", "studio", "dist");

  rmSync(releaseStudioBuildDirectory, { force: true, recursive: true });
  mkdirSync(releaseStudioBuildDirectory, { recursive: true });
  writeFileSync(
    path.join(releaseStudioBuildDirectory, "package.json"),
    `${JSON.stringify(
      {
        private: true,
        dependencies: {
          "orcheo-studio":
            studioSpec.replace(/^orcheo-studio@?/, "") || "latest",
        },
      },
      null,
      2,
    )}\n`,
  );

  run("npm", ["install", "--silent"], { cwd: releaseStudioBuildDirectory });
  run(
    "npm",
    [
      "--prefix",
      path.join(releaseStudioBuildDirectory, "node_modules", "orcheo-studio"),
      "run",
      "build",
    ],
    {
      env: {
        ...process.env,
        VITE_ORCHEO_AUTH_DISABLED:
          process.env.VITE_ORCHEO_AUTH_DISABLED ?? "true",
      },
    },
  );

  if (!existsSync(path.join(builtStudioDist, "index.html"))) {
    console.error(
      `Published Studio package did not build dist at ${builtStudioDist}.`,
    );
    process.exit(1);
  }

  rmSync(targetStudioDist, { force: true, recursive: true });
  cpSync(builtStudioDist, targetStudioDist, { recursive: true });
  console.log(`Built Studio from published package ${studioSpec}.`);
  process.exit(0);
}

const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
const result = spawnSync(npmCommand, ["run", "build"], {
  cwd: studioDirectory,
  env: {
    ...process.env,
    VITE_ORCHEO_AUTH_DISABLED: process.env.VITE_ORCHEO_AUTH_DISABLED ?? "true",
  },
  stdio: "inherit",
});

if (result.error) {
  throw result.error;
}

process.exit(result.status ?? 1);
