"use strict";

const {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  shell
} = require("electron");
const fs = require("node:fs");
const fsp = require("node:fs/promises");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const {
  backendSpawnSpec,
  isAllowedRendererUrl,
  normalizeExportFiles
} = require("./runtime.cjs");

const APP_ID = "com.flowlab.desktop";
const BACKEND_START_TIMEOUT_MS = 45_000;
const BACKEND_POLL_INTERVAL_MS = 150;
const ROOT = path.resolve(__dirname, "..", "..");

let mainWindow = null;
let backend = null;
let backendLogFd = null;
let backendPort = null;
let shuttingDown = false;

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function availablePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0 }, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : null;
      server.close((error) => {
        if (error) reject(error);
        else if (port === null) reject(new Error("Could not allocate a local FlowLab backend port."));
        else resolve(port);
      });
    });
  });
}

function healthRequest(port) {
  return new Promise((resolve) => {
    const request = http.get(
      {
        hostname: "127.0.0.1",
        port,
        path: "/api/health",
        timeout: 1_000
      },
      (response) => {
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          body += chunk;
        });
        response.on("end", () => {
          if (response.statusCode !== 200) {
            resolve(false);
            return;
          }
          try {
            const payload = JSON.parse(body);
            resolve(payload.status === "ok" && payload.service === "flowlab-solver");
          } catch {
            resolve(false);
          }
        });
      }
    );
    request.on("timeout", () => request.destroy());
    request.on("error", () => resolve(false));
  });
}

async function waitForBackend(port) {
  const deadline = Date.now() + BACKEND_START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (backend && backend.exitCode !== null) {
      throw new Error(`The FlowLab backend exited with code ${backend.exitCode}.`);
    }
    if (await healthRequest(port)) return;
    await delay(BACKEND_POLL_INTERVAL_MS);
  }
  throw new Error(`The FlowLab backend did not become healthy within ${BACKEND_START_TIMEOUT_MS / 1_000} seconds.`);
}

function augmentedPath(platform) {
  const current = process.env.PATH || "";
  const additions = platform === "darwin"
    ? ["/usr/local/bin", "/opt/homebrew/bin", "/Applications/Docker.app/Contents/Resources/bin"]
    : [path.join(process.env.ProgramFiles || "C:\\Program Files", "Docker", "Docker", "resources", "bin")];
  return [current, ...additions].filter(Boolean).join(path.delimiter);
}

function backendEnvironment(port, resourcesPath, packaged) {
  const supportRoot = app.getPath("userData");
  return {
    ...process.env,
    PATH: augmentedPath(process.platform),
    PYTHONDONTWRITEBYTECODE: "1",
    FLOWLAB_BACKEND_PORT: String(port),
    FLOWLAB_DESKTOP_DIST: packaged ? path.join(resourcesPath, "dist") : path.join(ROOT, "dist"),
    FLOWLAB_RUNTIME_DIR: path.join(supportRoot, "runtime")
  };
}

async function startBackend() {
  backendPort = await availablePort();
  const supportRoot = app.getPath("userData");
  await fsp.mkdir(supportRoot, { recursive: true });
  const logPath = path.join(supportRoot, "flowlab-backend.log");
  backendLogFd = fs.openSync(logPath, "a");

  const spec = backendSpawnSpec({
    appRoot: ROOT,
    resourcesPath: process.resourcesPath,
    packaged: app.isPackaged,
    platform: process.platform
  });
  backend = spawn(spec.command, spec.args, {
    cwd: supportRoot,
    env: backendEnvironment(backendPort, process.resourcesPath, app.isPackaged),
    windowsHide: true,
    stdio: ["ignore", backendLogFd, backendLogFd]
  });
  backend.on("error", (error) => {
    if (!shuttingDown) showFatalError(`The local solver service could not start: ${error.message}`);
  });
  backend.on("exit", (code, signal) => {
    if (!shuttingDown && mainWindow) {
      showFatalError(`The local solver service stopped unexpectedly (${signal || code || "unknown"}).`);
    }
  });

  await waitForBackend(backendPort);
}

function loadingDocument() {
  return `<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="color-scheme" content="dark">
<title>FlowLab</title>
<style>
html,body{height:100%;margin:0;background:#071118;color:#e7f7ff;font:15px system-ui}
body{display:grid;place-items:center}.status{display:flex;gap:12px;align-items:center}
.dot{width:10px;height:10px;border-radius:50%;background:#4fd1c5;box-shadow:0 0 18px #4fd1c5}
</style>
<div class="status"><span class="dot"></span><span>Starting the local FlowLab solver service…</span></div>
</html>`;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1512,
    height: 982,
    minWidth: 1180,
    minHeight: 760,
    show: false,
    backgroundColor: "#071118",
    title: "FlowLab",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      preload: path.join(__dirname, "preload.cjs")
    }
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-attach-webview", (event) => event.preventDefault());
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (backendPort !== null && !isAllowedRendererUrl(url, backendPort)) event.preventDefault();
  });
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  void mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(loadingDocument())}`);
}

function configureRendererSecurity() {
  if (!mainWindow || backendPort === null) {
    throw new Error("Renderer security cannot be configured before the local backend.");
  }
  const rendererSession = mainWindow.webContents.session;
  rendererSession.setPermissionCheckHandler(() => false);
  rendererSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
  rendererSession.webRequest.onHeadersReceived(
    { urls: [`http://127.0.0.1:${backendPort}/*`] },
    (details, callback) => {
      const headers = { ...details.responseHeaders };
      headers["Content-Security-Policy"] = [
        "default-src 'self'; "
          + "script-src 'self'; "
          + "style-src 'self' 'unsafe-inline'; "
          + "img-src 'self' data: blob:; "
          + "connect-src 'self'; "
          + "font-src 'self' data:; "
          + "worker-src 'self' blob:; "
          + "object-src 'none'; "
          + "base-uri 'self'; "
          + "frame-ancestors 'none'"
      ];
      headers["X-Content-Type-Options"] = ["nosniff"];
      headers["Referrer-Policy"] = ["no-referrer"];
      callback({ responseHeaders: headers });
    }
  );
}

function assertTrustedRenderer(event) {
  if (backendPort === null || !isAllowedRendererUrl(event.senderFrame.url, backendPort)) {
    throw new Error("Desktop file operations are restricted to the local FlowLab application.");
  }
}

async function writeExport(destination, file) {
  const temporary = `${destination}.flowlab-tmp-${process.pid}-${Date.now()}`;
  await fsp.writeFile(temporary, file.text, { encoding: "utf8", mode: 0o600 });
  try {
    if (process.platform === "win32" && fs.existsSync(destination)) {
      // Windows rename does not replace an existing destination. copyFile
      // replaces only after the temporary payload is complete, preserving the
      // user's original file if writing the temporary payload fails.
      await fsp.copyFile(temporary, destination);
      await fsp.rm(temporary, { force: true });
    } else {
      await fsp.rename(temporary, destination);
    }
  } catch (error) {
    await fsp.rm(temporary, { force: true });
    throw error;
  }
}

async function saveFiles(event, value) {
  assertTrustedRenderer(event);
  const files = normalizeExportFiles(value);
  if (!mainWindow) throw new Error("The FlowLab window is unavailable.");

  if (files.length === 1) {
    const file = files[0];
    const selection = await dialog.showSaveDialog(mainWindow, {
      title: "Export from FlowLab",
      defaultPath: file.filename,
      properties: ["createDirectory", "showOverwriteConfirmation"]
    });
    if (selection.canceled || !selection.filePath) {
      return { status: "cancelled", message: "Export cancelled." };
    }
    await writeExport(selection.filePath, file);
    return { status: "saved", message: `Exported ${path.basename(selection.filePath)}.` };
  }

  const selection = await dialog.showOpenDialog(mainWindow, {
    title: "Choose a FlowLab export folder",
    buttonLabel: "Export",
    properties: ["openDirectory", "createDirectory"]
  });
  if (selection.canceled || selection.filePaths.length !== 1) {
    return { status: "cancelled", message: "Export cancelled." };
  }
  const directory = selection.filePaths[0];
  const collisions = files
    .map((file) => path.join(directory, file.filename))
    .filter((destination) => fs.existsSync(destination));
  if (collisions.length > 0) {
    const confirmation = await dialog.showMessageBox(mainWindow, {
      type: "warning",
      title: "Replace existing FlowLab exports?",
      message: `${collisions.length} export file${collisions.length === 1 ? "" : "s"} already exist in this folder.`,
      detail: collisions.map((candidate) => path.basename(candidate)).join("\n"),
      buttons: ["Cancel", "Replace"],
      defaultId: 0,
      cancelId: 0,
      noLink: true
    });
    if (confirmation.response !== 1) {
      return { status: "cancelled", message: "Export cancelled." };
    }
  }
  for (const file of files) {
    await writeExport(path.join(directory, file.filename), file);
  }
  return { status: "saved", message: `Exported ${files.length} files.` };
}

function showFatalError(message) {
  if (mainWindow) {
    void dialog.showMessageBox(mainWindow, {
      type: "error",
      title: "FlowLab could not continue",
      message,
      detail: `Backend log: ${path.join(app.getPath("userData"), "flowlab-backend.log")}`
    });
  }
}

async function writeSmokeResult() {
  const output = process.env.FLOWLAB_DESKTOP_SMOKE_OUTPUT;
  if (!output || backendPort === null) return false;
  const payload = {
    schema: "flowlab.electron_package_smoke.v1",
    platform: process.platform,
    architecture: process.arch,
    packaged: app.isPackaged,
    backendHealthy: await healthRequest(backendPort),
    uiReachable: await new Promise((resolve) => {
      const request = http.get(`http://127.0.0.1:${backendPort}/`, (response) => {
        response.resume();
        resolve(response.statusCode === 200);
      });
      request.on("error", () => resolve(false));
    })
  };
  await fsp.writeFile(output, `${JSON.stringify(payload, null, 2)}${os.EOL}`, "utf8");
  return true;
}

function stopBackend() {
  shuttingDown = true;
  if (backend && backend.exitCode === null) backend.kill();
  backend = null;
  if (backendLogFd !== null) {
    fs.closeSync(backendLogFd);
    backendLogFd = null;
  }
}

const lockAcquired = app.requestSingleInstanceLock();
if (!lockAcquired) {
  app.quit();
} else {
  app.setAppUserModelId(APP_ID);
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.on("before-quit", stopBackend);
  app.on("window-all-closed", () => app.quit());
  ipcMain.handle("flowlab:save-files", async (event, files) => {
    try {
      return await saveFiles(event, files);
    } catch (error) {
      return {
        status: "error",
        message: error instanceof Error ? error.message : "Export failed."
      };
    }
  });

  app.whenReady().then(async () => {
    createWindow();
    try {
      await startBackend();
      configureRendererSecurity();
      if (await writeSmokeResult()) {
        app.quit();
        return;
      }
      await mainWindow?.loadURL(`http://127.0.0.1:${backendPort}/`);
    } catch (error) {
      showFatalError(error instanceof Error ? error.message : "The local solver service failed to start.");
    }
  });
}
