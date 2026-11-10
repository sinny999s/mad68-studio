<script lang="ts">
  import FixedScrollArea from "$lib/components/fixed-scroll-area.svelte"
  import Switch from "$lib/components/switch.svelte"
  import { Label } from "$lib/components/ui/label"
  import * as RadioGroup from "$lib/components/ui/radio-group"
  import { hasLastPressedStick } from "$lib/libhmk/commands/gamepad-state"
  import { cn, type WithoutChildren } from "$lib/utils"
  import type { HTMLAttributes } from "svelte/elements"
  import { gamepadLiveContext } from "../queries/gamepad-live.svelte"
  import { gamepadQueryContext } from "../queries/gamepad-query.svelte"
  import AnalogCurve from "./analog-curve/analog-curve.svelte"

  const {
    class: className,
    ...props
  }: WithoutChildren<HTMLAttributes<HTMLDivElement>> = $props()

  const gamepadQuery = gamepadQueryContext.get()
  const { current: options } = $derived(gamepadQuery.gamepadOptions)
  const live = gamepadLiveContext.get()

  // What the stick does when both keys of one axis are held (like A and D).
  // "Last pressed" also sets the snappy bit, so firmware older than v7.2
  // (which ignores the new bit) falls back to the closest behaviour.
  type OppositeMode = "cancel" | "deeper" | "last"
  const oppositeModes: {
    value: OppositeMode
    title: string
    description: string
  }[] = [
    {
      value: "last",
      title: "Last Pressed Wins",
      description:
        "The key you pressed most recently takes over, at its own depth. Let it go and the stick goes back to the other key. Like Null Bind's Last Pressed, for the stick.",
    },
    {
      value: "deeper",
      title: "Deeper Key Wins",
      description:
        "The key pressed further sets the direction. When both are fully pressed, the stick points left or down. Formerly Snappy Joystick.",
    },
    {
      value: "cancel",
      title: "Cancel Out",
      description:
        "The two directions cancel each other, so holding both fully centres the stick.",
    },
  ]
  const oppositeMode = $derived<OppositeMode | undefined>(
    options &&
      (options.lastJoystick
        ? "last"
        : options.snappyJoystick
          ? "deeper"
          : "cancel"),
  )
  // Only known once the keyboard has answered; older firmware can't do it
  const lastSupported = $derived(hasLastPressedStick(live.state))
  const lastUnknown = $derived(live.state === null && live.supported !== false)
</script>

<div class={cn("grid grid-cols-[minmax(0,1fr)_28rem]", className)} {...props}>
  <FixedScrollArea class="flex flex-col gap-4 p-6 pt-2 pr-4">
    <div class="grid text-sm">
      <span class="font-bold">Configure Analog Curve</span>
      <span class="text-muted-foreground">
        Modify how keyboard analog values translate to gamepad analog inputs.
        You can manually adjust the analog curve or select a preset from below.
        These settings affect both joysticks and triggers.
      </span>
    </div>
    <AnalogCurve />
  </FixedScrollArea>
  <FixedScrollArea class="flex flex-col gap-4 p-6 pt-2 pl-4">
    <Switch
      bind:checked={
        () => options?.squareJoystick ?? false,
        (v) =>
          options &&
          gamepadQuery.setOptions({
            data: { ...options, squareJoystick: v },
          })
      }
      description="Remove the circular boundaries of the joystick for full range of motion."
      disabled={!options}
      id="square-joystick"
      title="Square Joystick Mode"
    />
    <div class="grid gap-3">
      <div class="grid gap-1">
        <span class="text-sm font-bold">Opposite Directions</span>
        <p class="text-sm text-wrap text-muted-foreground">
          What the stick does when you hold both keys of one direction pair,
          like A and D.
        </p>
      </div>
      <RadioGroup.Root
        bind:value={
          () => oppositeMode ?? "",
          (v) =>
            options &&
            gamepadQuery.setOptions({
              data: {
                ...options,
                snappyJoystick: v !== "cancel",
                lastJoystick: v === "last",
              },
            })
        }
        disabled={!options}
      >
        {#each oppositeModes as mode (mode.value)}
          {@const unavailable =
            mode.value === "last" && !lastSupported && oppositeMode !== "last"}
          <div class="flex items-start gap-3">
            <RadioGroup.Item
              class="mt-0.5"
              disabled={unavailable}
              id="opposite-{mode.value}"
              value={mode.value}
            />
            <div class={cn("grid gap-1", unavailable && "opacity-50")}>
              <Label for="opposite-{mode.value}">{mode.title}</Label>
              <p class="text-sm text-wrap text-muted-foreground">
                {#if unavailable && !lastUnknown}
                  Needs custom firmware v7.2 or later. Install it from the
                  Firmware page.
                {:else}
                  {mode.description}
                {/if}
              </p>
            </div>
          </div>
        {/each}
      </RadioGroup.Root>
    </div>
  </FixedScrollArea>
</div>
