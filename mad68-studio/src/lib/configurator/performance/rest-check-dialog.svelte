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

<!--
Rest check: a hands-off measurement of how far each key reads at rest. This
file runs the check and shows its progress; the counting and the verdicts are
in rest-check.ts, the report in rest-check-result.svelte. The check only reads
the keys' travel, the same reading the page's live key travel uses.
-->
<script lang="ts">
  import { Button } from "$lib/components/ui/button"
  import * as Dialog from "$lib/components/ui/dialog"
  import { Label } from "$lib/components/ui/label"
  import * as RadioGroup from "$lib/components/ui/radio-group"
  import { keyboardContext } from "$lib/keyboard"
  import { getKeycodeMetadata } from "$lib/keycodes"
  import { cn } from "$lib/utils"
  import { onDestroy, tick } from "svelte"
  import { toast } from "svelte-sonner"
  import { analogInfoQueryContext } from "../queries/analog-info-query.svelte"
  import { keymapQueryContext } from "../queries/keymap-query.svelte"
  import {
    formatCountdown,
    formatReading,
    HAND_PRESS_MM,
    measureAtRest,
    REST_CHECK_DURATIONS,
    REST_CHECK_LEAD_IN_MS,
    sleep,
    type RestCheckEnd,
    type RestMeasurement,
  } from "./rest-check"
  import RestCheckResult from "./rest-check-result.svelte"

  const keyboard = keyboardContext.get()
  const analogInfoQuery = analogInfoQueryContext.get()
  const { current: keymap } = $derived(keymapQueryContext.get().keymap)

  // The page's live key travel is drawn about this often
  const LIVE_TRAVEL_INTERVAL_MS = 1000 / 30
  // How often the count of readings and the keys that moved are brought up to
  // date while a check runs
  const PROGRESS_INTERVAL_MS = 250

  let open = $state(false)
  let content = $state<HTMLElement | null>(null)
  let seconds = $state(REST_CHECK_DURATIONS[0].seconds)

  // The last check that got to measure. Kept for as long as the page is open,
  // so the dialog can be closed, an actuation point changed, and the result
  // looked at again.
  let result = $state.raw<{
    measurement: RestMeasurement
    ended: RestCheckEnd
  } | null>(null)
  // With a result on screen: back to choosing how long to measure
  let choosing = $state(false)

  // The check in progress: three seconds to let go of the keyboard, then the
  // measuring
  let run = $state.raw<{
    controller: AbortController
    durationMs: number
    since: number
    measuring: boolean
  } | null>(null)
  let now = $state(0)
  let progress = $state.raw<{
    readings: number
    moved: { key: number; deepest: number }[]
  }>({ readings: 0, moved: [] })

  $effect(() => {
    if (!run) return
    const timer = setInterval(() => (now = performance.now()), 200)
    return () => clearInterval(timer)
  })

  const left = $derived(
    run
      ? Math.max(
          0,
          (run.measuring ? run.durationMs : REST_CHECK_LEAD_IN_MS) -
            (now - run.since),
        )
      : 0,
  )

  // A key is called what it types on the first layer
  const keyName = (key: number) => {
    const keycode = keymap?.[0]?.[key]
    const name =
      keycode === undefined ? "" : getKeycodeMetadata(keycode).name.trim()
    return name.split("\n").at(-1) || `Key ${key + 1}`
  }

  // While a check runs nothing else reads the keys, and the demo keyboard
  // stops typing
  function hold(on: boolean) {
    analogInfoQuery.paused = on
    keyboard.setSimulatedTyping?.(!on)
  }

  async function start() {
    if (run) return

    const controller = new AbortController()
    const { signal } = controller
    const durationMs = seconds * 1000

    now = performance.now()
    run = { controller, durationMs, since: now, measuring: false }
    progress = { readings: 0, moved: [] }
    hold(true)

    // The Start button is gone: keep the focus off the Cancel button, so a
    // key that presses by itself cannot end the check
    await tick()
    content?.focus()

    try {
      await sleep(REST_CHECK_LEAD_IN_MS, signal)
      if (signal.aborted) return

      now = performance.now()
      run = { controller, durationMs, since: now, measuring: true }

      let drawnAt = 0
      let countedAt = 0
      const { measurement, ended } = await measureAtRest({
        read: async () => {
          const analogInfo = await keyboard.analogInfo()
          // Keep the keyboard behind the dialog live with the same readings
          if (
            !signal.aborted &&
            performance.now() - drawnAt >= LIVE_TRAVEL_INTERVAL_MS
          ) {
            drawnAt = performance.now()
            analogInfoQuery.analogInfo.mutate(analogInfo)
          }
          return analogInfo
        },
        numKeys: keyboard.metadata.numKeys,
        durationMs,
        signal,
        onReading: ({ readings, deepest }) => {
          if (performance.now() - countedAt < PROGRESS_INTERVAL_MS) return
          countedAt = performance.now()
          progress = {
            readings,
            moved: deepest
              .map((v, key) => ({ key, deepest: v }))
              .filter(({ deepest }) => deepest > 0)
              .sort((a, b) => b.deepest - a.deepest),
          }
        },
      })

      if (ended === "cancelled") return
      if (measurement.readings === 0) {
        toast.error("The keyboard didn't answer, so nothing was measured")
        return
      }
      result = { measurement, ended }
      choosing = false
    } finally {
      // Unless this check was cancelled and another one has started since
      if (!run || run.controller === controller) {
        run = null
        hold(false)
      }
    }
  }

  function cancel() {
    if (!run) return
    run.controller.abort()
    run = null
    hold(false)
  }

  onDestroy(cancel)
</script>

<Dialog.Root
  bind:open={
    () => open,
    (v) => {
      if (!v) cancel()
      open = v
    }
  }
>
  <Dialog.Trigger>
    {#snippet child({ props })}
      <Button size="sm" variant="outline" {...props}>Rest Check</Button>
    {/snippet}
  </Dialog.Trigger>
  <Dialog.Content
    bind:ref={content}
    class={cn(
      "gap-4",
      !run && result && !choosing ? "sm:max-w-4xl" : "sm:max-w-lg",
    )}
    escapeKeydownBehavior={run ? "ignore" : "close"}
    interactOutsideBehavior={run ? "ignore" : "close"}
    showCloseButton={!run}
  >
    <Dialog.Header>
      <Dialog.Title>Rest Check</Dialog.Title>
      <Dialog.Description>
        How far each key reads while nobody is touching the keyboard, and how
        much room that leaves under its actuation point.
      </Dialog.Description>
    </Dialog.Header>

    {#if run}
      <div class="grid gap-3">
        <p class="text-base font-medium" role="status">
          Keep your hands off the keyboard.
        </p>
        {#if run.measuring}
          <div class="grid gap-1.5">
            <div class="flex items-baseline justify-between gap-4">
              <span class="text-2xl font-semibold tabular-nums">
                {formatCountdown(left)}
              </span>
              <span class="text-sm text-muted-foreground">left</span>
            </div>
            <div
              aria-label="Rest check progress"
              aria-valuemax={100}
              aria-valuemin={0}
              aria-valuenow={Math.round(100 - (left / run.durationMs) * 100)}
              class="h-1.5 overflow-hidden rounded-full bg-muted"
              role="progressbar"
            >
              <div
                class="h-full rounded-full bg-primary"
                style:width="{100 - (left / run.durationMs) * 100}%"
              ></div>
            </div>
          </div>
          <p class="text-sm text-wrap text-muted-foreground">
            Every key read {progress.readings.toLocaleString("en-US")}
            {progress.readings === 1 ? "time" : "times"} so far.
            {#if progress.moved.length === 0}
              No key has moved.
            {:else}
              Moved:
              {progress.moved
                .slice(0, 6)
                .map(
                  ({ key, deepest }) =>
                    `${keyName(key)} ${formatReading(deepest)} mm`,
                )
                .join(", ")}{progress.moved.length > 6
                ? ` and ${progress.moved.length - 6} more`
                : ""}.
            {/if}
          </p>
        {:else}
          <p class="text-2xl font-semibold tabular-nums">
            Starting in {Math.ceil(left / 1000)}
          </p>
        {/if}
        <p class="text-sm text-wrap text-muted-foreground">
          To stop early, click Cancel. The Esc key does nothing while the check
          runs, so a key that presses by itself cannot end it.
          {#if keyboard.demo}
            The demo keyboard stops its simulated typing until the check is
            over.
          {/if}
        </p>
      </div>
      <Dialog.Footer>
        <Button onclick={cancel} size="sm" variant="outline">Cancel</Button>
      </Dialog.Footer>
    {:else if result && !choosing}
      <RestCheckResult {...result} {keyName} />
      <Dialog.Footer>
        <Button onclick={() => (choosing = true)} size="sm" variant="outline">
          Check Again
        </Button>
        <Dialog.Close>
          {#snippet child({ props })}
            <Button size="sm" {...props}>Done</Button>
          {/snippet}
        </Dialog.Close>
      </Dialog.Footer>
    {:else}
      <div class="grid gap-3">
        <p class="text-sm text-wrap text-muted-foreground">
          A resting key should read 0.00 mm, but a sensor can give a stray
          reading now and then, and a key with a low actuation point can then
          press by itself. The check reads every key's travel as fast as the
          keyboard answers and keeps the deepest reading of each key. Start it,
          then take your hands off the keyboard until it is done. A key that
          goes past {HAND_PRESS_MM.toFixed(2)} mm counts as pressed by hand and is
          left out.
        </p>
        <div class="grid gap-2">
          <span class="text-sm font-bold">How long to measure</span>
          <RadioGroup.Root
            bind:value={() => String(seconds), (v) => (seconds = Number(v))}
          >
            {#each REST_CHECK_DURATIONS as duration (duration.seconds)}
              <div class="flex items-center gap-3">
                <RadioGroup.Item
                  id="rest-check-{duration.seconds}"
                  value={String(duration.seconds)}
                />
                <Label for="rest-check-{duration.seconds}">
                  {duration.title}
                </Label>
              </div>
            {/each}
          </RadioGroup.Root>
        </div>
        <p class="text-sm text-wrap text-muted-foreground">
          The result only covers the time the check ran, so a longer check finds
          rarer movements. The check only reads the keys: it changes nothing on
          the keyboard.
        </p>
      </div>
      <Dialog.Footer>
        {#if result}
          <Button
            onclick={() => (choosing = false)}
            size="sm"
            variant="outline"
          >
            Back to Result
          </Button>
        {/if}
        <Button onclick={start} size="sm">Start</Button>
      </Dialog.Footer>
    {/if}
  </Dialog.Content>
</Dialog.Root>
