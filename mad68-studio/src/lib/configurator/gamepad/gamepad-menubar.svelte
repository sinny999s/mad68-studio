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
  import * as KeyboardEditor from "$lib/components/keyboard-editor"
  import Switch from "$lib/components/switch.svelte"
  import { keyboardContext } from "$lib/keyboard"
  import { restartReturnsToApp } from "$lib/libhmk/commands/gamepad-state"
  import { markRestart } from "$lib/restart"
  import { toast } from "svelte-sonner"
  import { gamepadLiveContext } from "../queries/gamepad-live.svelte"
  import { optionsQueryContext } from "../queries/options-query.svelte"

  const keyboard = keyboardContext.get()
  const optionsQuery = optionsQueryContext.get()
  const { current: options } = $derived(optionsQuery.options)
  const live = gamepadLiveContext.get()

  let restarting = $state(false)

  // The controller interface is part of the keyboard's USB description, so
  // Windows only sees the change after the keyboard reconnects. Restart it
  // for the user instead of asking them to unplug it.
  async function setInterface(enabled: boolean) {
    if (!options || restarting) return
    await optionsQuery.set({ data: { ...options, xInputEnabled: enabled } })
    if (keyboard.demo) return
    if (!restartReturnsToApp(live.state)) {
      // Older firmware would stay in update mode after a restart
      toast.info(
        "Saved. Unplug the keyboard and plug it back in so Windows sees the change. With firmware v6.1 the app does this for you.",
        { duration: 12000 },
      )
      return
    }
    restarting = true
    markRestart("gamepad", enabled ? "test" : "setup")
    toast.info(
      enabled
        ? "Restarting the keyboard so Windows sees the controller..."
        : "Restarting the keyboard to remove the controller...",
    )
    // The keyboard restarts straight away, so there is no reply to wait for
    keyboard.reboot().catch(() => {})
  }

  const outputKnown = $derived(
    live.supported === true && !!live.state?.interfaceEnabled,
  )
</script>

<KeyboardEditor.Menubar>
  <div class="flex items-center gap-6">
    <Switch
      bind:checked={
        () => options?.xInputEnabled ?? false, (v) => setInterface(v)
      }
      disabled={!options || restarting}
      id="xinput-enabled"
      title="Controller"
      tooltip={restartReturnsToApp(live.state)
        ? "Make your keyboard also show up as an Xbox controller, so games can read how far you press each bound key. The keyboard restarts by itself for about a second to apply this. It applies to all profiles."
        : "Make your keyboard also show up as an Xbox controller, so games can read how far you press each bound key. Unplug the keyboard and plug it back in to apply this (firmware v6.1 does it for you). It applies to all profiles."}
    />
    {#if outputKnown && live.state}
      <Switch
        bind:checked={
          () => live.state?.active ?? true,
          (v) => live.setActive(v).catch((e) => toast.error(String(e)))
        }
        id="gamepad-output"
        title={live.state.active ? "Output on" : "Output off"}
        tooltip="Switch controller output on and off without restarting. While it is off, keys bound to the controller type normally and the controller rests at centre. Fn+G does the same from the keyboard. Output is on again after the keyboard is unplugged."
      />
    {/if}
  </div>
  <KeyboardEditor.LayoutDialog />
</KeyboardEditor.Menubar>
