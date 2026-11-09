/*
 * This program is free software: you can redistribute it and/or modify it under
 * the terms of the GNU General Public License as published by the Free Software
 * Foundation, either version 3 of the License, or (at your option) any later
 * version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
 * FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
 * details.
 *
 * You should have received a copy of the GNU General Public License along with
 * this program. If not, see <https://www.gnu.org/licenses/>.
 */

// Desktop-only features, provided by the Electron preload (electron/preload.cjs)
// as window.studio. Absent when the configurator runs in a browser.

export type WindowedApp = { exe: string; title: string }

export type DesktopApi = {
  processes(): Promise<string[]>
  windowedApps(): Promise<WindowedApp[]>
  setBackground(on: boolean): Promise<boolean>
}

export function desktop(): DesktopApi | null {
  if (typeof window === "undefined") return null
  return (window as unknown as { studio?: DesktopApi }).studio ?? null
}
