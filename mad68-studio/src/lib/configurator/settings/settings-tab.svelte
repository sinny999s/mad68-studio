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
    estimateSmoothingDelay,
    SCAN_SPEED_REFUSED,
    SENSORS_PER_SCAN,
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

  // Key scan speed (custom firmware v7.3; the last three from v7.4). The
  // keyboard measures its own scan rate, so keep asking while this page is
  // open. `settle`, `mode` and `sample` are what the firmware uses, for when
  // it is too old to say.
  const scanSpeeds = [
    {
      title: "Relaxed",
      description: "The timing of custom firmware v7.2.",
      settle: 40,
      mode: 0,
      sample: 2,
    },
    {
      title: "Standard",
      description:
        "About as fast as MadLions' own firmware. Recommended, and the default.",
      settle: 28,
      mode: 0,
      sample: 2,
    },
    {
      title: "Fast",
      description: "Faster than MadLions' own firmware.",
      settle: 18,
      mode: 0,
      sample: 2,
    },
    {
      title: "Fastest",
      description:
        "About twice as fast as MadLions' own firmware, and the fastest that reads the sensors one row at a time.",
      settle: 8,
      mode: 0,
      sample: 2,
    },
    {
      title: "Turbo",
      description:
        "Reads three rows of sensors at once, with the same sensor timing as Fastest. Nearly three times as fast as MadLions' own firmware.",
      settle: 8,
      mode: 3,
      sample: 2,
    },
    {
      title: "Extreme",
      description:
        "Three rows at once and a shorter wait. About three and a half times as fast as MadLions' own firmware.",
      settle: 5,
      mode: 3,
      sample: 2,
    },
    {
      title: "Max",
      description:
        "Like Extreme, with shorter sensor sampling. About four times as fast as MadLions' own firmware.",
      settle: 5,
      mode: 3,
      sample: 1,
    },
  ]
  // Smoothing of the key readings (custom firmware v7.4)
  const smoothingLevels = [
    {
      title: "Classic",
      description:
        "The original smoothing, and the default. It works over time, so it is just as far behind at every scan speed.",
    },
    {
      title: "Smooth",
      description: "Works over the last readings of each key. The steadiest.",
    },
    {
      title: "Balanced",
      description: "Less smoothing, so the keyboard reacts sooner.",
    },
    {
      title: "Fast",
      description:
        "The least smoothing and the quickest reaction. Lets the most sensor noise through.",
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

  async function chooseSmoothing(level: number) {
    if (!scan || scan.smoothing === null || level === scan.smoothing) return
    scanSaving = true
    try {
      scan = await keyboard.setScanSmoothing(level)
    } catch {
      toast.error("The keyboard didn't take the new smoothing")
    } finally {
      scanSaving = false
    }
  }

  const formatRate = (n: number) =>
    (Math.round(n / 10) * 10).toLocaleString("en-US")
  /** Sensor readings per second, as the "K" figure keyboards are sold with */
  const formatTotal = (scansPerSecond: number) =>
    `${Math.round((scansPerSecond * SENSORS_PER_SCAN) / 1000)}K`
  const formatDelay = (ms: number) =>
    ms >= 0.95 ? `${ms.toFixed(1)} ms` : `${ms.toFixed(2)} ms`
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
              How often the keyboard reads all of its key sensors. The first
              four speeds read one row of sensors at a time and differ in how
              long each column of sensors is given to power up. The last three
              read three rows at once. Changes apply at once and are saved on
              the keyboard, which then re-reads every key's resting position, so
              keep your hands off the keys for a moment.
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
                  >
                  full scans per second ({formatTotal(scan.scanRate)} sensor readings).
                {:else}
                  Measuring the scan rate…
                {/if}
              </p>
              {#if scan.state === SCAN_SPEED_REFUSED && !scan.capturing}
                <p class="text-sm text-wrap text-destructive" role="alert">
                  Your keyboard is not using {scanSpeeds[scan.speed]?.title ??
                    "this speed"}: reading three rows at once did not give the
                  same key readings on it, so it is running Fastest instead.
                  Choose another speed, or choose this one again to retry.
                </p>
              {/if}
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
              {@const settle = scan?.settleUs[i] ?? speed.settle}
              {@const rate = estimateScanRate(
                settle,
                scan?.modes[i] ?? speed.mode,
                scan?.samples[i] ?? speed.sample,
              )}
              {@const missing = !!scan && i >= scan.settleUs.length}
              <div class="flex items-start gap-3">
                <RadioGroup.Item
                  class="mt-0.5"
                  disabled={missing}
                  id="scan-speed-{i}"
                  value={String(i)}
                />
                <div
                  class={cn("grid gap-1", (!scan || missing) && "opacity-50")}
                >
                  <Label for="scan-speed-{i}">
                    {speed.title}
                    <span class="font-normal text-muted-foreground">
                      · {settle} µs wait per column, about {formatRate(rate)} scans
                      per second ({formatTotal(rate)})
                    </span>
                  </Label>
                  <p class="text-sm text-wrap text-muted-foreground">
                    {speed.description}
                    {#if missing}
                      Needs custom firmware v7.4.
                    {/if}
                  </p>
                </div>
              </div>
            {/each}
          </RadioGroup.Root>
          <p class="text-sm text-wrap text-muted-foreground">
            Turbo, Extreme and Max were tested on one keyboard: millions of
            scans without a fault, and key readings within 0.3% of the slowest
            speed. The keyboard checks them itself before using them and falls
            back to Fastest if its readings don't agree. If key depth looks
            jumpy or keys press by themselves on yours, go back to Standard.
          </p>
        </div>
        <div class="grid gap-3">
          <div class="grid gap-1">
            <span class="text-sm font-bold">Key Smoothing</span>
            <p class="text-sm text-wrap text-muted-foreground">
              The keyboard smooths each key's readings before it acts on them,
              which keeps a resting key steady but puts the keyboard a little
              behind your finger. Classic smooths over time. The other three
              smooth over the last few readings of each key and ignore a single
              stray reading, so they get quicker as the scan speed goes up: at
              the slower scan speeds they are behind Classic, from Turbo up they
              are ahead of it. Each level shows how far behind it is at your
              scan speed. If a key's depth flickers at rest, or keys with a very
              low actuation point press by themselves, pick a steadier level.
            </p>
            {#if !scan || scan.smoothing === null}
              <p class="text-sm text-wrap text-muted-foreground">
                Needs custom firmware v7.4 or later. Install it from the
                Firmware page.
              </p>
            {/if}
          </div>
          <RadioGroup.Root
            bind:value={
              () =>
                scan && scan.smoothing !== null ? String(scan.smoothing) : "",
              (v) => chooseSmoothing(Number(v))
            }
            disabled={!scan || scan.smoothing === null || scanSaving}
          >
            {#each smoothingLevels as level, i (level.title)}
              {@const usable = !!scan && scan.smoothing !== null}
              <div class="flex items-start gap-3">
                <RadioGroup.Item
                  class="mt-0.5"
                  id="scan-smoothing-{i}"
                  value={String(i)}
                />
                <div class={cn("grid gap-1", !usable && "opacity-50")}>
                  <Label for="scan-smoothing-{i}">
                    {level.title}
                    {#if scan && usable && scan.scanRate > 0}
                      <span class="font-normal text-muted-foreground">
                        · about {formatDelay(
                          estimateSmoothingDelay(
                            i,
                            scan.scanRate,
                            scan.loopRate,
                          ),
                        )} behind at this scan speed
                      </span>
                    {/if}
                  </Label>
                  <p class="text-sm text-wrap text-muted-foreground">
                    {level.description}
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
