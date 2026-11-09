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
  import FixedScrollArea from "$lib/components/fixed-scroll-area.svelte"
  import { KeycodeButton } from "$lib/components/keycode-button"
  import Switch from "$lib/components/switch.svelte"
  import { Button } from "$lib/components/ui/button"
  import { gamepadButtonToKeycode } from "$lib/keycodes/gamepad"
  import { HMK_GamepadButton } from "$lib/libhmk/gamepad"
  import { unitToStyle } from "$lib/ui"
  import { cn, type WithoutChildren } from "$lib/utils"
  import { toast } from "svelte-sonner"
  import type { HTMLAttributes } from "svelte/elements"
  import { gamepadStateContext } from "../context.svelte"
  import { gamepadButtons } from "../lib/gamepad"
  import {
    applyLayoutPreset,
    gamepadLayoutPresets,
    type GamepadLayoutPreset,
  } from "../lib/gamepad-presets"
  import { gamepadQueryContext } from "../queries/gamepad-query.svelte"
  import { keymapQueryContext } from "../queries/keymap-query.svelte"

  const {
    class: className,
    ...props
  }: WithoutChildren<HTMLAttributes<HTMLDivElement>> = $props()

  const gamepadState = gamepadStateContext.get()
  const { key } = $derived(gamepadState)

  const gamepadQuery = gamepadQueryContext.get()
  const { current: options } = $derived(gamepadQuery.gamepadOptions)
  const { current: bound } = $derived(gamepadQuery.gamepadButtons)
  const { current: keymap } = $derived(keymapQueryContext.get().keymap)

  async function applyPreset(preset: GamepadLayoutPreset) {
    if (!bound || !keymap) return
    const { buttons, missing } = applyLayoutPreset(bound, keymap[0], preset)
    await gamepadQuery.setButtons({ offset: 0, data: buttons })
    // Keys that also typed would send games two inputs at once
    if (options && options.keyboardEnabled && !options.gamepadOverride) {
      await gamepadQuery.setOptions({
        data: { ...options, gamepadOverride: true },
      })
      toast.success(
        `${preset.name} set up. Gamepad Override is now on, so these keys only drive the controller; Fn+G gives you the keys back.`,
      )
    } else {
      toast.success(`${preset.name} set up.`)
    }
    if (missing.length)
      toast.warning(
        `${missing.length} key(s) of this layout are not on layer 0 of this profile and were skipped.`,
      )
  }

  async function clearAll() {
    if (!bound) return
    await gamepadQuery.setButtons({
      offset: 0,
      data: bound.map(() => HMK_GamepadButton.NONE),
    })
    toast.success("All controller bindings removed from this profile.")
  }
</script>

<div class={cn("grid grid-cols-[minmax(0,1fr)_28rem]", className)} {...props}>
  <FixedScrollArea class="flex flex-col gap-4 p-6 pt-2 pr-4">
    <div class="grid gap-2 text-sm">
      <div class="grid">
        <span class="font-bold">Quick Setup</span>
        <span class="text-muted-foreground">
          Bind a ready-made layout to this profile in one click.
        </span>
      </div>
      <div class="flex flex-wrap gap-2">
        {#each gamepadLayoutPresets as preset (preset.name)}
          <Button
            disabled={!bound || !keymap}
            onclick={() => applyPreset(preset)}
            size="sm"
            title={preset.description}
            variant="outline"
          >
            {preset.name}
          </Button>
        {/each}
        <Button
          disabled={!bound || !bound.some((b) => b !== HMK_GamepadButton.NONE)}
          onclick={clearAll}
          size="sm"
          variant="ghost"
        >
          Clear all
        </Button>
      </div>
    </div>
    <div class="grid text-sm">
      <span class="font-bold">Configure Controller Bindings</span>
      <span class="text-muted-foreground">
        Select a key on the keyboard above, then pick what it does on the
        controller. Right-click a key to unbind it.
      </span>
    </div>
    <div class="flex flex-wrap text-sm">
      {#each gamepadButtons as button (button)}
        <div class="p-0.5" style={unitToStyle()}>
          <KeycodeButton
            keycode={gamepadButtonToKeycode(button)}
            onclick={() => {
              if (key === null) return
              gamepadQuery.setButtons({ offset: key, data: [button] })
              gamepadState.key = null
            }}
            showTooltip
          />
        </div>
      {/each}
    </div>
  </FixedScrollArea>
  <FixedScrollArea class="flex flex-col gap-4 p-6 pt-2 pl-4">
    <Switch
      bind:checked={
        () => options?.keyboardEnabled ?? false,
        (v) =>
          options &&
          gamepadQuery.setOptions({
            data: { ...options, keyboardEnabled: v, gamepadOverride: false },
          })
      }
      description="Allow keyboard inputs to be sent along with gamepad inputs."
      disabled={!options}
      id="keyboard-enabled"
      title="Enable Keyboard Inputs"
    />
    <Switch
      bind:checked={
        () => options?.gamepadOverride ?? false,
        (v) =>
          options &&
          gamepadQuery.setOptions({
            data: { ...options, gamepadOverride: v },
          })
      }
      description="Disable keyboard inputs on keys bound to gamepad buttons."
      disabled={!options || !options.keyboardEnabled}
      id="gamepad-override"
      title="Gamepad Override"
    />
  </FixedScrollArea>
</div>
