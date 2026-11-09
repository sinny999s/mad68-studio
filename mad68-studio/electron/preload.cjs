// MAD68 Studio preload: the few desktop features the page may use, exposed
// as window.studio (the page itself has no Node access).

const { contextBridge, ipcRenderer } = require("electron")

contextBridge.exposeInMainWorld("studio", {
  /** Lower-case file names (e.g. "game.exe") of all running programs */
  processes: () => ipcRenderer.invoke("studio:processes"),
  /** Programs with a visible window: [{ exe, title }] */
  windowedApps: () => ipcRenderer.invoke("studio:windowed-apps"),
  /** Keep running in the tray when the window is closed */
  setBackground: (on) => ipcRenderer.invoke("studio:set-background", !!on),
})
