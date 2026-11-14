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
  import CommitSlider from "$lib/components/commit-slider.svelte"
  import FixedScrollArea from "$lib/components/fixed-scroll-area.svelte"
  import * as KeyTester from "$lib/components/key-tester"
  import Switch from "$lib/components/switch.svelte"
  import { Button } from "$lib/components/ui/button"
  import { keyboardContext } from "$lib/keyboard"
  import { isFeatureAvailable } from "$lib/utils"
  import { toast } from "svelte-sonner"
  import { analogInfoQueryContext } from "../queries/analog-info-query.svelte"
  import { calibrationQueryContext } from "../queries/calibration.query.svelte"
  import { optionsQueryContext } from "../queries/options-query.svelte"
  import { typingPauseContext } from "./typing-pause.svelte"

  const keyboard = keyboardContext.get()
  const {
    demo,
    version,
    metadata: { adcResolution, numKeys },
  } = keyboard

  const analogInfoQuery = analogInfoQueryContext.get()
  const calibrationQuery = calibrationQueryContext.get()
  const optionsQuery = optionsQueryContext.get()
  const typingPause = typingPauseContext.get()
  const { current: calibration } = $derived(calibrationQuery.calibration)
  const { current: options } = $derived(optionsQuery.options)
  const canSave = isFeatureAvailable("saveCalibrationThreshold", version)

  let recalibrating = $state(false)

  async function recalibrate() {
    recalibrating = true
    try {
      // Pause typing first, so the keys pressed next don't type
      if (typingPause.supported) await typingPause.beginCalibration()
      await analogInfoQuery.recalibrate()
      // Readings taken while the keyboard measured its rest values don't count
      typingPause.pressed.clear()
    } finally {
      recalibrating = false
    }
  }

  async function saveThreshold() {
    await keyboard.saveCalibrationThreshold()
    toast.success("Calibration saved on the keyboard")
  }

  async function finish() {
    typingPause.stop()
    if (canSave && !demo)
      await saveThreshold().catch(() => {
        toast.error("Couldn't save the calibration")
      })
  }
</script>

<div class="grid size-full grid-cols-[minmax(0,1fr)_24rem]">
  <FixedScrollArea class="flex flex-col gap-4 p-4">
    {#if typingPause.calibrating}
      <div
        class="flex flex-col gap-2 rounded-md border border-primary/60 bg-primary/10 p-3 text-sm"
      >
        <div class="font-bold">Typing paused</div>
        <p>
          Press every key all the way down, one at a time. Nothing reaches your
          PC, so no shortcut or Windows key can go off. Keys light up once they
          have been pressed all the way.
        </p>
        <p class="text-muted-foreground">
          {typingPause.pressed.size} of {numKeys} keys pressed all the way
        </p>
        <div>
          <Button onclick={finish} size="sm">
            {canSave && !demo ? "Done (save calibration)" : "Done"}
          </Button>
        </div>
      </div>
    {/if}
    {#if !isFeatureAvailable("saveCalibrationThreshold", version)}
      <Switch
        bind:checked={
          () => options?.saveBottomOutThreshold ?? false,
          (v) =>
            options &&
            optionsQuery.set({
              data: { ...options, saveBottomOutThreshold: v },
            })
        }
        disabled={demo || !options}
        id="save-bottom-out-threshold"
        title="Save Bottom Out Threshold"
        description="Periodically save the per-key bottom-out threshold values after some inactivity to be restored on next boot. The saved values will only be cleared on recalibration. This setting applies globally across all profiles."
      />
    {/if}
    <CommitSlider
      bind:committed={
        () => calibration?.initialRestValue ?? 0,
        (v) =>
          calibration &&
          calibrationQuery.set({
            data: {
              ...calibration,
              initialRestValue: v,
            },
          })
      }
      description="The initial noise floor represents the estimated analog value when a key is at rest. It should be set slightly higher than the actual analog readings of all keys in their resting position to prevent deadzones. Recalibrate the keyboard to apply changes. This setting applies globally across all profiles."
      disabled={demo || !calibration}
      min={0}
      max={(1 << adcResolution) - 1}
      step={10}
      title="Initial Noise Floor"
    />
    <CommitSlider
      bind:committed={
        () =>
          calibration?.initialBottomOutThreshold ?? (1 << adcResolution) - 1,
        (v) =>
          calibration &&
          calibrationQuery.set({
            data: {
              ...calibration,
              initialBottomOutThreshold: v,
            },
          })
      }
      description="The initial bottom out threshold represents the estimated change in analog value when a key is fully pressed. It should be set slightly lower than the actual change in analog readings when keys are fully pressed to prevent deadzones. Recalibrate the keyboard to apply changes. This setting applies globally across all profiles."
      disabled={demo || !calibration}
      min={0}
      max={(1 << adcResolution) - 1}
      step={10}
      title="Initial Bottom Out Threshold"
    />
    <div class="flex flex-wrap gap-2">
      <Button
        disabled={recalibrating}
        onclick={recalibrate}
        size="sm"
        variant="destructive"
      >
        Recalibrate
      </Button>
      {#if typingPause.supported && !typingPause.calibrating}
        <Button
          onclick={() =>
            typingPause.paused ? typingPause.stop() : typingPause.start()}
          size="sm"
          variant={typingPause.paused ? "default" : "outline"}
        >
          {typingPause.paused ? "Resume Typing" : "Pause Typing"}
        </Button>
      {/if}
      {#if canSave && !typingPause.calibrating}
        <Button disabled={demo} onclick={saveThreshold} size="sm">
          Save Current Threshold
        </Button>
      {/if}
    </div>
    {#if typingPause.paused && !typingPause.calibrating}
      <p class="text-sm text-muted-foreground">
        Typing paused: keys don't reach your PC. It comes back when you click
        Resume Typing, leave this page or switch to another window.
      </p>
    {:else if typingPause.endedBecause}
      <p class="text-sm text-muted-foreground">
        Typing came back on because {typingPause.endedBecause}.
      </p>
    {:else if typingPause.supported === false && !demo}
      <p class="text-sm text-muted-foreground">
        To press keys here without typing, install custom firmware v7.6 or newer
        (Firmware page). Until then, click into an empty Notepad window before
        pressing every key.
      </p>
    {:else if typingPause.supported && !typingPause.calibrating}
      <p class="text-sm text-muted-foreground">
        Recalibrate pauses typing until you click Done, so pressing every key
        can't type or trigger shortcuts.
      </p>
    {/if}
  </FixedScrollArea>
  <FixedScrollArea class="flex flex-col gap-4 p-4">
    {#if typingPause.paused}
      <p class="text-sm text-muted-foreground">
        Typing is paused, so the key tester stays empty.
      </p>
    {/if}
    <KeyTester.Root>
      <div class="flex flex-col gap-2">
        <div class="text-sm font-bold">Pressed Keys</div>
        <KeyTester.Press class="h-32 w-full" />
      </div>
      <div class="flex flex-col gap-2">
        <div class="text-sm font-bold">Released Keys</div>
        <KeyTester.Release class="h-32 w-full" />
      </div>
    </KeyTester.Root>
  </FixedScrollArea>
</div>
