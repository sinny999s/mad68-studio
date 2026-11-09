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

<!-- Switches the keyboard's profile while a listed game runs (desktop app). -->
<script lang="ts">
  import { desktop } from "$lib/desktop"
  import { decide } from "$lib/game-profiles"
  import { gameProfiles } from "$lib/game-profiles.svelte"
  import { keyboardContext } from "$lib/keyboard"
  import { untrack } from "svelte"
  import { toast } from "svelte-sonner"

  const POLL_MS = 3000

  const keyboard = keyboardContext.get()
  const api = desktop()

  const watching = $derived(
    gameProfiles.settings.enabled && gameProfiles.settings.rules.length > 0,
  )

  // With games set up, closing the window keeps the app in the tray
  $effect(() => {
    api?.setBackground(watching).catch(() => {})
  })

  $effect(() => {
    if (!api || keyboard.demo || !watching) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const tick = async () => {
      if (stopped) return
      const rules = untrack(() => $state.snapshot(gameProfiles.settings.rules))
      try {
        const running = new Set(await api.processes())
        const current = await keyboard.getProfile()
        if (stopped) return
        const d = decide(
          rules,
          running,
          untrack(() => gameProfiles.watch),
          current,
        )
        if (d.switchTo !== null && gameProfiles.supported !== false) {
          try {
            await keyboard.setActiveProfile(d.switchTo)
            gameProfiles.supported = true
          } catch {
            gameProfiles.supported = false
            toast.error(
              "Switching profiles for games needs firmware v6. Install it from the Firmware page.",
            )
          }
        }
        gameProfiles.watch = d.state
        if (d.message && gameProfiles.supported !== false) toast.info(d.message)
      } catch {
        // keyboard busy or the process list failed: try again next time
      }
      if (!stopped) timer = setTimeout(tick, POLL_MS)
    }
    tick()

    return () => {
      stopped = true
      clearTimeout(timer)
    }
  })
</script>
