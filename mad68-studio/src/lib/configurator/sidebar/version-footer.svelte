<!--
This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
this program. If not, see <https://www.gnu.org/licenses/>.
-->

<script lang="ts">
  import { ArrowUpCircleIcon, DownloadIcon } from "@lucide/svelte"
  import * as Sidebar from "$lib/components/ui/sidebar"
  import { keyboardContext } from "$lib/keyboard"
  import { formatFirmwareRelease } from "$lib/release"
  import { APP_VERSION, updates } from "$lib/updates.svelte"
  import { displayVersion } from "$lib/utils"
  import { globalStateContext } from "../context.svelte"

  const { version } = keyboardContext.get()
  const globalState = globalStateContext.get()

  // The custom firmware release when the keyboard reports it (v7.2 and later)
  const firmware = $derived(
    updates.installed?.known
      ? `Firmware ${formatFirmwareRelease(updates.installed.release)}`
      : `libhmk ${displayVersion(version)}`,
  )
</script>

<Sidebar.Menu>
  {#if updates.firmwareOutdated}
    <Sidebar.MenuItem>
      <Sidebar.MenuButton
        class="h-8 text-xs font-semibold text-primary [&>svg]:text-primary"
        onclick={() => (globalState.tab = "firmware")}
        tooltipContent="Firmware update available"
      >
        <ArrowUpCircleIcon />
        <span>Firmware update available</span>
      </Sidebar.MenuButton>
    </Sidebar.MenuItem>
  {/if}
  {#if updates.app}
    <Sidebar.MenuItem>
      <Sidebar.MenuButton
        class="h-8 text-xs font-semibold text-primary [&>svg]:text-primary"
        onclick={() => updates.app && window.open(updates.app.url, "_blank")}
        tooltipContent="MAD68 Studio {updates.app.version} is available"
      >
        <DownloadIcon />
        <span>MAD68 Studio {updates.app.version} is available</span>
      </Sidebar.MenuButton>
    </Sidebar.MenuItem>
  {/if}
  <Sidebar.MenuItem>
    <div
      class="grid px-2 py-1 text-xs text-sidebar-foreground/70 group-data-[collapsible=icon]:hidden"
      title="libhmk {displayVersion(version)}"
    >
      <span class="truncate font-semibold">MAD68 Studio {APP_VERSION}</span>
      <span class="truncate">{firmware}</span>
    </div>
  </Sidebar.MenuItem>
</Sidebar.Menu>
