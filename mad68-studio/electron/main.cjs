// MAD68 Studio desktop shell.
//
// Serves the SvelteKit build from a private "app://" scheme (a secure context,
// which WebHID requires) and lets the page talk to MadLions keyboards
// (USB vendor 0x373B) without the browser device picker: the configurator
// connects as soon as the keyboard is plugged in, and the firmware updater
// can find the keyboard again when it restarts into update mode.

const {
  app,
  BrowserWindow,
  Menu,
  Tray,
  ipcMain,
  nativeImage,
  protocol,
  session,
  shell,
} = require("electron")
const { execFile } = require("node:child_process")
const fs = require("node:fs")
const path = require("node:path")

const MADLIONS_VID = 0x373b
const BUILD_DIR = path.join(__dirname, "..", "build")
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".webmanifest": "application/manifest+json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
  ".bin": "application/octet-stream",
}

protocol.registerSchemesAsPrivileged([
  {
    scheme: "app",
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
    },
  },
])

function resolveAsset(urlPath) {
  const rel = decodeURIComponent(urlPath).replace(/^\/+/, "")
  const file = path.normalize(path.join(BUILD_DIR, rel))
  // Stay inside the build folder; fall back to the SPA entry point
  if (
    file.startsWith(BUILD_DIR) &&
    fs.existsSync(file) &&
    fs.statSync(file).isFile()
  ) {
    return file
  }
  return path.join(BUILD_DIR, "index.html")
}

// Firmware installs are traced to %APPDATA%\MAD68 Studio\studio.log so a
// failed update can be diagnosed afterwards.
function logLine(line) {
  try {
    const file = path.join(app.getPath("userData"), "studio.log")
    fs.appendFileSync(
      file,
      `[${new Date().toISOString()}] ${line}
`,
    )
  } catch {
    // logging must never break the app
  }
}

function isMadlions(device) {
  return device && device.vendorId === MADLIONS_VID
}

// --- Games: running programs, for switching profiles automatically ---------

function listProcesses() {
  return new Promise((resolve) => {
    execFile(
      "tasklist.exe",
      ["/fo", "csv", "/nh"],
      { windowsHide: true, maxBuffer: 8 << 20, timeout: 10000 },
      (err, stdout) => {
        if (err) return resolve([])
        const names = new Set()
        for (const line of stdout.split(/\r?\n/)) {
          const m = /^"([^"]+)"/.exec(line)
          if (m) names.add(m[1].toLowerCase())
        }
        resolve([...names])
      },
    )
  })
}

// Programs with a visible window, for picking a game from what is open now
function listWindowedApps() {
  const script =
    "Get-Process | Where-Object { $_.MainWindowTitle } | " +
    "ForEach-Object { [pscustomobject]@{ exe = $_.ProcessName + '.exe'; title = $_.MainWindowTitle } } | " +
    "ConvertTo-Json -Compress"
  return new Promise((resolve) => {
    execFile(
      "powershell.exe",
      ["-NoProfile", "-NonInteractive", "-Command", script],
      { windowsHide: true, maxBuffer: 8 << 20, timeout: 20000 },
      (err, stdout) => {
        if (err) return resolve([])
        try {
          const parsed = JSON.parse(stdout.trim() || "[]")
          const list = Array.isArray(parsed) ? parsed : [parsed]
          resolve(
            list
              .filter((a) => a && typeof a.exe === "string")
              .map((a) => ({
                exe: a.exe.toLowerCase(),
                title: String(a.title ?? ""),
              })),
          )
        } catch {
          resolve([])
        }
      },
    )
  })
}

// --- Tray: keep running while games are set to switch profiles -------------

let keepInTray = false
let quitting = false
let tray = null
let trayHintShown = false

function showWindow() {
  const win = BrowserWindow.getAllWindows()[0]
  if (!win) return createWindow()
  if (win.isMinimized()) win.restore()
  win.show()
  win.focus()
}

function ensureTray() {
  if (tray) return tray
  const iconFile = path.join(BUILD_DIR, "web-app-manifest-192x192.png")
  const icon = nativeImage
    .createFromPath(iconFile)
    .resize({ width: 16, height: 16 })
  tray = new Tray(icon)
  tray.setToolTip("MAD68 Studio")
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Open MAD68 Studio", click: showWindow },
      { type: "separator" },
      {
        label: "Quit",
        click: () => {
          quitting = true
          app.quit()
        },
      },
    ]),
  )
  tray.on("click", showWindow)
  return tray
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 1024,
    minHeight: 768,
    title: "MAD68 Studio",
    backgroundColor: "#0a0a0a",
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.cjs"),
      // Keep game detection running while the window is hidden in the tray
      backgroundThrottling: false,
    },
  })
  win.on("close", (event) => {
    if (!keepInTray || quitting) return
    event.preventDefault()
    win.hide()
    const t = ensureTray()
    if (!trayHintShown) {
      trayHintShown = true
      t.displayBalloon({
        title: "MAD68 Studio is still running",
        content:
          "It keeps switching profiles for your games. Right-click the tray icon to quit.",
      })
    }
  })
  // External links open in the user's browser, not inside the app
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) shell.openExternal(url)
    return { action: "deny" }
  })
  win.webContents.on("console-message", (_e, _level, message) => {
    if (message.startsWith("[flash]")) logLine(message)
  })
  win.loadURL("app://studio/")
  if (process.env.MAD68_SMOKE) smokeTest(win)
  return win
}

// Development self-check: MAD68_SMOKE=1 prints what the app shows after
// connecting, opens some pages (MAD68_SMOKE_TABS, default Lighting and
// Firmware), prints those too, and quits. Read-only: it changes nothing.
function smokeTest(win) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms))
  const text = () =>
    win.webContents.executeJavaScript("document.body.innerText")
  win.webContents.on("console-message", (_e, level, message) => {
    if (level >= 2) console.log(`[renderer] ${message}`)
  })
  win.webContents.once("did-finish-load", async () => {
    await wait(7000)
    console.log("=== START SCREEN ===\n" + (await text()).slice(0, 1500))
    const studio = await win.webContents.executeJavaScript(
      `(async () => window.studio ? (await window.studio.processes()).length : -1)()`,
    )
    console.log(`=== DESKTOP API === processes seen: ${studio}`)
    const tabs = (process.env.MAD68_SMOKE_TABS || "Lighting,Firmware").split(
      ",",
    )
    for (const tab of tabs) {
      await win.webContents.executeJavaScript(`(() => {
        const b = [...document.querySelectorAll("button,[role=tab]")]
          .find((e) => e.innerText.trim() === ${JSON.stringify(tab)})
        if (b) b.click()
        return !!b
      })()`)
      await wait(2500)
      console.log(
        `=== ${tab.toUpperCase()} ===\n` + (await text()).slice(0, 1500),
      )
    }
    app.quit()
  })
}

// One window only: starting the app again brings the running one forward
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on("second-instance", showWindow)
}

app.on("before-quit", () => {
  quitting = true
})

ipcMain.handle("studio:processes", () => listProcesses())
ipcMain.handle("studio:windowed-apps", () => listWindowedApps())
ipcMain.handle("studio:set-background", (_event, on) => {
  keepInTray = !!on
  if (!keepInTray && tray) {
    tray.destroy()
    tray = null
  }
  return keepInTray
})

app.whenReady().then(() => {
  // Files are read with Node rather than net.fetch(file://): Chromium's file
  // loader fails on Windows paths longer than 260 characters, which deep
  // install folders easily reach.
  protocol.handle("app", async (request) => {
    const { pathname } = new URL(request.url)
    const file = resolveAsset(pathname)
    try {
      const body = await fs.promises.readFile(file)
      const type =
        MIME[path.extname(file).toLowerCase()] || "application/octet-stream"
      return new Response(body, {
        headers: { "content-type": type, "cache-control": "no-cache" },
      })
    } catch (err) {
      console.error(`[app://] ${request.url}: ${err}`)
      return new Response("Not found", { status: 404 })
    }
  })

  const ses = session.defaultSession
  ses.setPermissionCheckHandler((_wc, permission) =>
    permission === "hid" ? true : null,
  )
  ses.setDevicePermissionHandler(
    (details) => details.deviceType === "hid" && isMadlions(details.device),
  )
  // navigator.hid.requestDevice(): pick the matching MadLions device
  // automatically (or none, which resolves to an empty list).
  ses.on("select-hid-device", (event, details, callback) => {
    event.preventDefault()
    const device = details.deviceList.find(isMadlions)
    callback(device ? device.deviceId : "")
  })

  createWindow()
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit()
})
