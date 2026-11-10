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
  import Switch from "$lib/components/switch.svelte"
  import { Button } from "$lib/components/ui/button"
  import * as Dialog from "$lib/components/ui/dialog"
  import { Label } from "$lib/components/ui/label"
  import * as RadioGroup from "$lib/components/ui/radio-group"
  import { keyboardContext } from "$lib/keyboard"
  import { restartReturnsToApp } from "$lib/libhmk/commands/gamepad-state"
  import {
    estimateScanRate,
    type ScanSpeedInfo,
  } from "$lib/libhmk/commands/scan-speed"
  import { markRestart } from "$lib/restart"
  import { cn, isFeatureAvailable, type WithoutChildren } from "$lib/utils"
  import { toast } from "svelte-sonner"
  import type { HTMLAttributes } from "svelte/elements"
  import { optionsQueryContext } from "../queries/options-query.svelte"
  import { profileQueryContext } from "../queries/profile-query.svelte"

  const {
    class: className,
    ...props
  }: WithoutChildren<HTMLAttributes<HTMLDivElement>> = $props()

  const keyboard = keyboardContext.get()
  const {
    demo,
    version,
    metadata: { usbHighSpeed },
  } = keyboard

  // Restarting from the app only comes back by itself from firmware v6.1
  let canRestart = $state(false)
  $effect(() => {
    keyboard
      .getGamepadState()
      .then((s) => (canRestart = restartReturnsToApp(s)))
  })

  const profileQuery = profileQueryContext.get()
  const optionsQuery = optionsQueryContext.get()
  const { current: options } = $derived(optionsQuery.options)

  // Key scan speed (custom firmware v7.3). The keyboard measures its own scan
  // rate, so keep asking while this page is open.
  const scanSpeeds = [
    {
      title: "Relaxed",
      description: "The timing of custom firmware v7.2.",
    },
    {
      title: "Standard",
      description:
        "About as fast as MadLions' own firmware. Recommended, and the default.",
    },
    {
      title: "Fast",
      description: "Faster than MadLions' own firmware.",
    },
    {
      title: "Fastest",
      description:
        "About twice as fast as MadLions' own firmware. Tested on one keyboard with no change in key readings. If key depth looks jumpy or keys press by themselves on yours, go back to Standard.",
    },
  ]
  let scan = $state<ScanSpeedInfo | null>(null)
  let scanChecked = $state(false)
  let scanSaving = $state(false)
  $effect(() => {
    let stopped = false
    const poll = () =>
      keyboard
        .getScanSpeed()
        .catch(() => null)
        .then((s) => {
          if (stopped) return
          if (!scanSaving) scan = s
          scanChecked = true
        })
    poll()
    const timer = setInterval(() => scan && poll(), 1000)
    return () => {
      stopped = true
      clearInterval(timer)
    }
  })

  async function chooseScanSpeed(speed: number) {
    if (!scan || speed === scan.speed) return
    scanSaving = true
    try {
      scan = await keyboard.setScanSpeed(speed)
    } catch {
      toast.error("The keyboard didn't take the new scan speed")
    } finally {
      scanSaving = false
    }
  }

  const formatRate = (n: number) =>
    (Math.round(n / 10) * 10).toLocaleString("en-US")
</script>

<div
  class={cn("mx-auto flex size-full max-w-3xl flex-col", className)}
  {...props}
>
  <FixedScrollArea class="flex flex-col gap-4 p-4">
    <div
      class="flex flex-col divide-y rounded-2xl border bg-card shadow-sm [&>*]:p-5"
    >
      {#if usbHighSpeed && isFeatureAvailable("pollingRateSwitch", version)}
        <Switch
          bind:checked={
            () => options?.highPollingRateEnabled ?? false,
            (v) =>
              options &&
              optionsQuery.set({
                data: { ...options, highPollingRateEnabled: v },
              })
          }
          id="8000hz-polling-rate"
          title="8000Hz Polling Rate"
          description="Enable the 8000Hz polling rate for faster response times, but may increase the CPU usage of the host device. Restart the keyboard to apply changes. This setting applies globally across all profiles."
        />
      {/if}
      {#if scanChecked}
        <div class="grid gap-3">
          <div class="grid gap-1">
            <span class="text-sm font-bold">Key Scan Speed</span>
            <p class="text-sm text-wrap text-muted-foreground">
              How long the keyboard lets each column of key sensors power up
              before reading it. Shorter means every key is read more often.
              Changes apply at once and are saved on the keyboard, which then
              re-reads every key's resting position, so keep your hands off the
              keys for a moment.
            </p>
            {#if scan}
              <p class="text-sm text-wrap" aria-live="polite">
                {#if scan.capturing}
                  Re-reading every key's resting position…
                {:else if scan.scanRate > 0}
                  {demo
                    ? "Demo estimate (a real keyboard measures its own):"
                    : "Measured on your keyboard:"}
                  <span class="font-bold"
                    >{scan.scanRate.toLocaleString("en-US")}</span
                  > full scans per second.
                {:else}
                  Measuring the scan rate…
                {/if}
              </p>
            {:else}
              <p class="text-sm text-wrap text-muted-foreground">
                Needs custom firmware v7.3 or later. Install it from the
                Firmware page.
              </p>
            {/if}
          </div>
          <RadioGroup.Root
            bind:value={
              () => (scan ? String(scan.speed) : ""),
              (v) => chooseScanSpeed(Number(v))
            }
            disabled={!scan || scanSaving}
          >
            {#each scanSpeeds as speed, i (speed.title)}
              {@const settle = scan?.settleUs[i] ?? [40, 28, 18, 8][i]}
              <div class="flex items-start gap-3">
                <RadioGroup.Item
                  class="mt-0.5"
                  id="scan-speed-{i}"
                  value={String(i)}
                />
                <div class={cn("grid gap-1", !scan && "opacity-50")}>
                  <Label for="scan-speed-{i}">
                    {speed.title}
                    <span class="font-normal text-muted-foreground">
                      · {settle} µs per column, about {formatRate(
                        estimateScanRate(settle),
                      )} scans per second
                    </span>
                  </Label>
                  <p class="text-sm text-wrap text-muted-foreground">
                    {speed.description}
                  </p>
                </div>
              </div>
            {/each}
          </RadioGroup.Root>
        </div>
      {/if}
      <div class="flex flex-col gap-2">
        <div class="grid text-sm text-wrap">
          <span class="font-bold">Restart Keyboard</span>
          <span class="text-muted-foreground">
            {#if canRestart || demo}
              The keyboard will disconnect and reconnect. No changes will be
              made to your keyboard settings.
            {:else}
              On this firmware a restart from the app leaves the keyboard in
              update mode, so unplug it and plug it back in instead. Firmware
              v6.1 fixes this.
            {/if}
          </span>
        </div>
        <div>
          <Button
            disabled={demo || !canRestart}
            onclick={() => {
              markRestart("settings")
              keyboard.reboot().catch(() => {})
            }}
            size="sm"
            variant="outline"
          >
            Restart Keyboard
          </Button>
        </div>
      </div>
      <div class="flex flex-col gap-2">
        <div class="grid text-sm text-wrap">
          <span class="font-bold">Enter Bootloader Mode</span>
          <span class="text-muted-foreground">
            The keyboard will restart and enter bootloader mode if it is
            supported by the firmware. No changes will be made to your keyboard
            settings.
          </span>
        </div>
        <div>
          <Button
            disabled={demo}
            onclick={() => keyboard.bootloader()}
            size="sm"
            variant="outline"
          >
            Enter Bootloader Mode
          </Button>
        </div>
      </div>
      <div class="flex flex-col gap-2">
        <div class="grid text-sm text-wrap">
          <span class="font-bold">Factory Reset</span>
          <span class="text-muted-foreground">
            Revert the keyboard to its factory settings defined by the firmware.
            All user data and settings will be lost.
          </span>
        </div>
        <div>
          <Dialog.Root>
            <Dialog.Trigger>
              {#snippet child({ props })}
                <Button
                  disabled={demo}
                  size="sm"
                  variant="destructive"
                  {...props}
                >
                  Factory Reset
                </Button>
              {/snippet}
            </Dialog.Trigger>
            <Dialog.Content>
              <Dialog.Header>
                <Dialog.Title>Factory Reset?</Dialog.Title>
                <Dialog.Description>
                  Are you sure you want to factory reset your keyboard?
                </Dialog.Description>
              </Dialog.Header>
              <Dialog.Footer>
                <Dialog.Close>
                  {#snippet child({ props })}
                    <Button size="sm" variant="outline" {...props}
                      >Cancel</Button
                    >
                  {/snippet}
                </Dialog.Close>
                <Dialog.Close onclick={() => profileQuery.factoryReset()}>
                  {#snippet child({ props })}
                    <Button size="sm" variant="destructive" {...props}>
                      Factory Reset
                    </Button>
                  {/snippet}
                </Dialog.Close>
              </Dialog.Footer>
            </Dialog.Content>
          </Dialog.Root>
        </div>
      </div>
    </div>
  </FixedScrollArea>
</div>
