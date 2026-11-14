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
  import * as KeyButton from "$lib/components/key-button"
  import { KeyboardEditorKeyboard } from "$lib/components/keyboard-editor"
  import { displayDistance } from "$lib/distance"
  import { analogInfoQueryContext } from "../queries/analog-info-query.svelte"
  import { typingPauseContext } from "./typing-pause.svelte"

  const { current: analogInfo } = $derived(
    analogInfoQueryContext.get().analogInfo,
  )
  const typingPause = typingPauseContext.get()

  // During a guided calibration, light up each key once it has been pressed
  // all the way
  $effect(() => {
    if (analogInfo) typingPause.track(analogInfo)
  })
</script>

<KeyboardEditorKeyboard>
  {#snippet keyGenerator(key)}
    {#if !analogInfo}
      <KeyButton.Skeleton />
    {:else}
      <KeyButton.Root
        size="sm"
        data-state={typingPause.calibrating && typingPause.pressed.has(key)
          ? "on"
          : "off"}
      >
        <KeyButton.Depth distance={analogInfo[key].distance} />
        <span class="font-mono">{analogInfo[key].adcValue}</span>
        <span class="text-muted-foreground"
          >{displayDistance(analogInfo[key].distance)}</span
        >
      </KeyButton.Root>
    {/if}
  {/snippet}
</KeyboardEditorKeyboard>
