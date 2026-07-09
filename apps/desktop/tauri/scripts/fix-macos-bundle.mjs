import { spawnSync } from "node:child_process";
import { Buffer } from "node:buffer";
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

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

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const tauriDirectory = path.resolve(__dirname, "..");
const stagedPlaywright = path.join(tauriDirectory, "bundle", "ms-playwright");

if (process.platform !== "darwin") {
  process.exit(0);
}

const hasStagedPlaywright = existsSync(stagedPlaywright);

if (!hasStagedPlaywright) {
  console.log(
    "No staged Playwright browsers found; skipping macOS bundle fix-up.",
  );
}

const macosBundleDir = path.join(
  tauriDirectory,
  "src-tauri",
  "target",
  "release",
  "bundle",
  "macos",
);

if (!existsSync(macosBundleDir)) {
  console.log("No macOS app bundle found; skipping fix-up.");
  process.exit(0);
}

function run(command, args) {
  const result = spawnSync(command, args, { stdio: "inherit" });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function runCapture(command, args) {
  const result = spawnSync(command, args, { encoding: "utf8" });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.stdout.write(result.stdout);
    process.stderr.write(result.stderr);
    process.exit(result.status ?? 1);
  }
  return result.stdout;
}

function runWithInput(command, args, input) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    input,
    stdio: ["pipe", "inherit", "inherit"],
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function makeCrcTable() {
  const table = new Uint32Array(256);
  for (let i = 0; i < table.length; i += 1) {
    let value = i;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[i] = value >>> 0;
  }
  return table;
}

const crcTable = makeCrcTable();

function crc32(buffer) {
  let value = 0xffffffff;
  for (const byte of buffer) {
    value = crcTable[(value ^ byte) & 0xff] ^ (value >>> 8);
  }
  return (value ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuffer = Buffer.from(type, "ascii");
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])), 0);
  return Buffer.concat([length, typeBuffer, data, checksum]);
}

function createInstallArrowBackground() {
  const width = 560;
  const height = 360;
  const pixels = Buffer.alloc((width * 4 + 1) * height);

  function setPixel(x, y, color) {
    if (x < 0 || x >= width || y < 0 || y >= height) {
      return;
    }
    const offset = y * (width * 4 + 1) + 1 + x * 4;
    pixels[offset] = color[0];
    pixels[offset + 1] = color[1];
    pixels[offset + 2] = color[2];
    pixels[offset + 3] = color[3];
  }

  function fill(color) {
    for (let y = 0; y < height; y += 1) {
      pixels[y * (width * 4 + 1)] = 0;
      for (let x = 0; x < width; x += 1) {
        setPixel(x, y, color);
      }
    }
  }

  function drawLine(x1, y1, x2, y2, thickness, color) {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const lengthSquared = dx * dx + dy * dy;
    const radius = thickness / 2;
    for (
      let y = Math.floor(Math.min(y1, y2) - radius);
      y <= Math.ceil(Math.max(y1, y2) + radius);
      y += 1
    ) {
      for (
        let x = Math.floor(Math.min(x1, x2) - radius);
        x <= Math.ceil(Math.max(x1, x2) + radius);
        x += 1
      ) {
        const t = Math.max(
          0,
          Math.min(1, ((x - x1) * dx + (y - y1) * dy) / lengthSquared),
        );
        const nearestX = x1 + t * dx;
        const nearestY = y1 + t * dy;
        if (Math.hypot(x - nearestX, y - nearestY) <= radius) {
          setPixel(x, y, color);
        }
      }
    }
  }

  function sign(px, py, ax, ay, bx, by) {
    return (px - bx) * (ay - by) - (ax - bx) * (py - by);
  }

  function drawTriangle(ax, ay, bx, by, cx, cy, color) {
    const minX = Math.floor(Math.min(ax, bx, cx));
    const maxX = Math.ceil(Math.max(ax, bx, cx));
    const minY = Math.floor(Math.min(ay, by, cy));
    const maxY = Math.ceil(Math.max(ay, by, cy));
    for (let y = minY; y <= maxY; y += 1) {
      for (let x = minX; x <= maxX; x += 1) {
        const d1 = sign(x, y, ax, ay, bx, by);
        const d2 = sign(x, y, bx, by, cx, cy);
        const d3 = sign(x, y, cx, cy, ax, ay);
        const hasNegative = d1 < 0 || d2 < 0 || d3 < 0;
        const hasPositive = d1 > 0 || d2 > 0 || d3 > 0;
        if (!(hasNegative && hasPositive)) {
          setPixel(x, y, color);
        }
      }
    }
  }

  fill([246, 247, 249, 255]);
  drawLine(228, 162, 330, 162, 12, [52, 61, 72, 255]);
  drawTriangle(358, 162, 322, 140, 322, 184, [52, 61, 72, 255]);

  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 6;
  header[10] = 0;
  header[11] = 0;
  header[12] = 0;

  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk("IHDR", header),
    pngChunk("IDAT", deflateSync(pixels, { level: 9 })),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

function escapeAppleScriptString(value) {
  return value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function parseMountPoint(output) {
  const matches = [...output.matchAll(/\/Volumes\/[^\n]+/g)];
  if (matches.length === 0) {
    throw new Error(
      `Unable to find mounted DMG volume in hdiutil output:\n${output}`,
    );
  }
  return matches[matches.length - 1][0].trim();
}

function applyDmgFinderLayout(volumeName, mountPoint, appName) {
  const script = `
tell application "Finder"
  tell disk "${escapeAppleScriptString(volumeName)}"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 760, 480}
    set viewOptions to the icon view options of container window
    set arrangement of viewOptions to not arranged
    set icon size of viewOptions to 128
    set background picture of viewOptions to (POSIX file "${escapeAppleScriptString(path.join(mountPoint, ".background", "background.png"))}" as alias)
    set position of item "${escapeAppleScriptString(appName)}" of container window to {140, 180}
    set position of item "Applications" of container window to {420, 180}
    update without registering applications
    delay 1
    close
  end tell
end tell
`;
  runWithInput("osascript", [], script);
}

function rebuildDmg(dmgPath, appPath, appName) {
  const volumeName = path.basename(appName, ".app");
  const tempDir = mkdtempSync(path.join(os.tmpdir(), "orcheo-dmg-"));
  const stagingDir = path.join(tempDir, "staging");
  const backgroundDir = path.join(stagingDir, ".background");
  const rwDmgPath = path.join(tempDir, `${volumeName}.rw.dmg`);
  let mountPoint = null;

  try {
    mkdirSync(backgroundDir, { recursive: true });
    run("ditto", [appPath, path.join(stagingDir, appName)]);
    symlinkSync("/Applications", path.join(stagingDir, "Applications"));
    writeFileSync(
      path.join(backgroundDir, "background.png"),
      createInstallArrowBackground(),
    );
    run("SetFile", ["-a", "V", backgroundDir]);

    run("hdiutil", [
      "create",
      "-volname",
      volumeName,
      "-srcfolder",
      stagingDir,
      "-ov",
      "-format",
      "UDRW",
      rwDmgPath,
    ]);

    const attachOutput = runCapture("hdiutil", [
      "attach",
      rwDmgPath,
      "-nobrowse",
      "-readwrite",
    ]);
    mountPoint = parseMountPoint(attachOutput);
    run("SetFile", ["-a", "V", path.join(mountPoint, ".background")]);
    applyDmgFinderLayout(volumeName, mountPoint, appName);
    run("sync", []);
    run("hdiutil", ["detach", mountPoint]);
    mountPoint = null;

    rmSync(dmgPath, { force: true });
    run("hdiutil", [
      "convert",
      rwDmgPath,
      "-format",
      "UDZO",
      "-imagekey",
      "zlib-level=9",
      "-o",
      dmgPath,
    ]);
  } finally {
    if (mountPoint !== null) {
      run("hdiutil", ["detach", mountPoint]);
    }
    rmSync(tempDir, { force: true, recursive: true });
  }
}

const appNames = readdirSync(macosBundleDir).filter((entry) =>
  entry.endsWith(".app"),
);

for (const appName of appNames) {
  const appPath = path.join(macosBundleDir, appName);
  const resourceDir = path.join(
    appPath,
    "Contents",
    "Resources",
    "ms-playwright",
  );

  if (!hasStagedPlaywright) {
    continue;
  }

  rmSync(resourceDir, { force: true, recursive: true });
  cpSync(stagedPlaywright, resourceDir, {
    dereference: false,
    recursive: true,
  });
  console.log(`Restored symlink-preserving Playwright browsers in ${appName}`);

  run("codesign", ["--force", "--deep", "--sign", "-", appPath]);
  console.log(`Re-signed ${appName} after fixing bundled resources`);
}

const dmgDir = path.join(
  tauriDirectory,
  "src-tauri",
  "target",
  "release",
  "bundle",
  "dmg",
);

if (existsSync(dmgDir) && appNames.length > 0) {
  const dmgFiles = readdirSync(dmgDir).filter((entry) =>
    entry.endsWith(".dmg"),
  );
  const appPath = path.join(macosBundleDir, appNames[0]);

  for (const dmgFile of dmgFiles) {
    const dmgPath = path.join(dmgDir, dmgFile);
    // Tauri's own styled dmg (custom icon/background/window layout) was
    // built from the broken .app and went stale the moment we fixed it
    // above, so rebuild one from the corrected .app and restore the standard
    // drag-to-Applications install layout.
    rebuildDmg(dmgPath, appPath, appNames[0]);
    console.log(
      `Rebuilt ${dmgFile} from the fixed .app with Applications shortcut`,
    );
  }
}
