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
  import { Button } from "$lib/components/ui/button"
  import { keyboardContext } from "$lib/keyboard"
  import {
    restartReturnsToApp,
    XINPUT_BUTTONS,
  } from "$lib/libhmk/commands/gamepad-state"
  import { HMK_GamepadButton } from "$lib/libhmk/gamepad"
  import { cn, type WithoutChildren } from "$lib/utils"
  import { toast } from "svelte-sonner"
  import type { HTMLAttributes } from "svelte/elements"
  import { gamepadStateContext, globalStateContext } from "../context.svelte"
  import { gamepadLiveContext } from "../queries/gamepad-live.svelte"
  import { gamepadQueryContext } from "../queries/gamepad-query.svelte"

  const {
    class: className,
    ...props
  }: WithoutChildren<HTMLAttributes<HTMLDivElement>> = $props()

  const keyboard = keyboardContext.get()
  const live = gamepadLiveContext.get()
  const s = $derived(live.state)
  const globalState = globalStateContext.get()
  const gamepadState = gamepadStateContext.get()
  const { current: bound } = $derived(gamepadQueryContext.get().gamepadButtons)
  const boundCount = $derived(
    bound ? bound.filter((b) => b !== HMK_GamepadButton.NONE).length : null,
  )

  type Status = {
    tone: "ok" | "warn" | "muted"
    text: string
    action?: { label: string; run: () => void }
  }

  const status: Status = $derived.by((): Status => {
    if (live.supported === false)
      return {
        tone: "muted",
        text: "This firmware cannot report the controller. Install firmware v6 from the Firmware page.",
      }
    if (!s) return { tone: "muted", text: "Reading the controller..." }
    if (!s.interfaceEnabled)
      return {
        tone: "warn",
        text: restartReturnsToApp(s)
          ? "The controller is off. Switch on Controller above; the keyboard restarts by itself."
          : "The controller is off. Switch on Controller above, then unplug the keyboard and plug it back in.",
      }
    if (!s.interfaceOpen)
      return {
        tone: "warn",
        text: "The controller is on, but Windows has not set it up yet. If this stays, unplug the keyboard and plug it back in.",
      }
    if (!s.active)
      return {
        tone: "warn",
        text: "Windows sees the controller, but output is off (Fn+G). Bound keys type instead.",
      }
    // The controller uses the keyboard's active profile, not the one shown here
    if (s.profile !== globalState.profile)
      return {
        tone: "warn",
        text: `The keyboard is using Profile ${s.profile}, but this page shows Profile ${globalState.profile}'s bindings, so they are not in use.`,
        action: {
          label: `Use Profile ${globalState.profile}`,
          run: () =>
            keyboard
              .setActiveProfile(globalState.profile)
              .catch((e) => toast.error(String(e))),
        },
      }
    if (boundCount === 0)
      return {
        tone: "warn",
        text: `No keys are bound to the controller in Profile ${s.profile} yet, so there is nothing to send.`,
        action: {
          label: "Bind keys",
          run: () => gamepadState.setTab("setup"),
        },
      }
    return {
      tone: "ok",
      text: "Windows sees the controller. Press your bound keys to see what games receive.",
    }
  })

  const pct = (v: number, max: number) => Math.round((v / max) * 100)

  const buttons: [string, number][] = [
    ["A", XINPUT_BUTTONS.A],
    ["B", XINPUT_BUTTONS.B],
    ["X", XINPUT_BUTTONS.X],
    ["Y", XINPUT_BUTTONS.Y],
    ["LB", XINPUT_BUTTONS.LB],
    ["RB", XINPUT_BUTTONS.RB],
    ["Back", XINPUT_BUTTONS.BACK],
    ["Start", XINPUT_BUTTONS.START],
    ["Guide", XINPUT_BUTTONS.HOME],
    ["LS", XINPUT_BUTTONS.LS],
    ["RS", XINPUT_BUTTONS.RS],
    ["Up", XINPUT_BUTTONS.UP],
    ["Down", XINPUT_BUTTONS.DOWN],
    ["Left", XINPUT_BUTTONS.LEFT],
    ["Right", XINPUT_BUTTONS.RIGHT],
  ]
</script>

{#snippet stickView(label: string, x: number, y: number)}
  {@const r = 46}
  {@const cx = 60 + (x / 32767) * r}
  {@const cy = 60 - (y / 32767) * r}
  {@const moved = x !== 0 || y !== 0}
  <div class="grid justify-items-center gap-2">
    <svg
      class="size-32"
      viewBox="0 0 120 120"
      role="img"
      aria-label="{label}: x {pct(x, 32767)}%, y {pct(y, 32767)}%"
    >
      <circle
        cx="60"
        cy="60"
        r={r + 6}
        class="fill-muted stroke-border"
        stroke-width="1"
      />
      <line x1="60" y1="8" x2="60" y2="112" class="stroke-border" />
      <line x1="8" y1="60" x2="112" y2="60" class="stroke-border" />
      <line
        x1="60"
        y1="60"
        x2={cx}
        y2={cy}
        class="stroke-primary/50"
        stroke-width="2"
      />
      <circle
        {cx}
        {cy}
        r="9"
        class={cn(moved ? "fill-primary" : "fill-muted-foreground/60")}
      />
    </svg>
    <div class="grid justify-items-center text-sm">
      <span class="font-medium">{label}</span>
      <span class="text-muted-foreground tabular-nums">
        x {pct(x, 32767)}% · y {pct(y, 32767)}%
      </span>
    </div>
  </div>
{/snippet}

{#snippet triggerView(label: string, value: number)}
  <div class="grid justify-items-center gap-2">
    <div
      class="relative h-32 w-8 overflow-hidden rounded-md border bg-muted"
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={255}
      aria-valuenow={value}
    >
      <div
        class="absolute inset-x-0 bottom-0 bg-primary"
        style:height="{pct(value, 255)}%"
      ></div>
    </div>
    <div class="grid justify-items-center text-sm">
      <span class="font-medium">{label}</span>
      <span class="text-muted-foreground tabular-nums">
        {pct(value, 255)}%
      </span>
    </div>
  </div>
{/snippet}

<div class={cn("grid grid-cols-[minmax(0,1fr)_28rem]", className)} {...props}>
  <FixedScrollArea class="flex flex-col gap-4 p-6 pt-2 pr-4">
    <div class="grid text-sm">
      <span class="font-medium">Test the Controller</span>
      <span class="text-muted-foreground">
        What the keyboard is sending as an Xbox controller right now, live.
      </span>
    </div>
    <div
      class={cn(
        "rounded-lg border p-3 text-sm",
        status.tone === "ok" && "border-primary/40 bg-primary/5",
        status.tone === "warn" && "border-amber-500/40 bg-amber-500/5",
        status.tone === "muted" && "text-muted-foreground",
      )}
      role="status"
    >
      <div class="flex flex-wrap items-center gap-3">
        <span class="flex-1">{status.text}</span>
        {#if status.action}
          <Button onclick={status.action.run} size="sm" variant="outline">
            {status.action.label}
          </Button>
        {/if}
      </div>
    </div>
    <div class="flex flex-wrap items-end justify-center gap-8 py-2">
      {@render triggerView("LT", s?.leftTrigger ?? 0)}
      {@render stickView("Left stick", s?.leftX ?? 0, s?.leftY ?? 0)}
      {@render stickView("Right stick", s?.rightX ?? 0, s?.rightY ?? 0)}
      {@render triggerView("RT", s?.rightTrigger ?? 0)}
    </div>
    <div class="flex flex-wrap justify-center gap-1.5">
      {#each buttons as [name, bit] (name)}
        {@const down = !!s && (s.buttons & bit) !== 0}
        <span
          class={cn(
            "min-w-12 rounded-md border px-2 py-1 text-center text-xs font-medium transition-colors",
            down
              ? "border-primary bg-primary text-primary-foreground"
              : "text-muted-foreground",
          )}
        >
          {name}
        </span>
      {/each}
    </div>
  </FixedScrollArea>
  <FixedScrollArea class="flex flex-col gap-4 p-6 pt-2 pl-4 text-sm">
    <div class="grid gap-1">
      <span class="font-medium">Switching between typing and playing</span>
      <span class="text-muted-foreground">
        Press <kbd class="rounded border px-1">Fn</kbd> +
        <kbd class="rounded border px-1">G</kbd> to switch controller output off and
        on, for example to type in chat. Bound keys flash green for on and blink red
        for off. No restart is needed.
      </span>
    </div>
    <div class="grid gap-1">
      <span class="font-medium">Check in Windows</span>
      <span class="text-muted-foreground">
        Press <kbd class="rounded border px-1">Win</kbd> +
        <kbd class="rounded border px-1">R</kbd>, type
        <code class="rounded bg-muted px-1">joy.cpl</code> and press Enter. The keyboard
        is listed as a controller, and Properties shows the same sticks.
      </span>
    </div>
    <div class="grid gap-1">
      <span class="font-medium">In games</span>
      <span class="text-muted-foreground">
        Games that support Xbox controllers read the sticks and triggers
        directly. Some games stop reading the keyboard while a controller is in
        use, or the other way round; switch output off with Fn+G when you need
        the keys back.
      </span>
    </div>
  </FixedScrollArea>
</div>
