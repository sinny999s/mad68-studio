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
  import {
    displayLayoutContext,
    type DisplayLayout,
  } from "$lib/configurator/context.svelte"
  import { unitToEM, unitToStyle } from "$lib/ui"
  import { cn, type WithoutChildren } from "$lib/utils"
  import type { Snippet } from "svelte"
  import type { HTMLAttributes } from "svelte/elements"

  const {
    class: className,
    displayLayout = displayLayoutContext.get(),
    keyGenerator,
    ...props
  }: WithoutChildren<HTMLAttributes<HTMLDivElement>> & {
    displayLayout?: DisplayLayout
    keyGenerator?: Snippet<[number]>
  } = $props()

  let containerWidth = $state(0)
  let containerHeight = $state(0)

  const fontSize = $derived(
    Math.min(
      containerWidth / unitToEM(displayLayout.width),
      containerHeight / unitToEM(displayLayout.height),
    ),
  )
</script>

<div class={cn("flex-1 p-4 select-none", className)} {...props}>
  <div
    bind:clientWidth={containerWidth}
    bind:clientHeight={containerHeight}
    class="relative h-full"
    style:font-size="{fontSize}px"
  >
    {#if fontSize > 0}
      <div class="absolute inset-0 grid place-items-center">
        <div
          class="relative"
          style={unitToStyle(displayLayout.width, displayLayout.height)}
        >
          <!-- The keyboard case behind the keycaps -->
          <div
            class="pointer-events-none absolute -inset-[0.45em] rounded-[0.7em] border border-case-edge bg-case shadow-[0_1.2em_2.4em_-1em_rgb(0_0_0/0.55),inset_0_1px_0_rgb(255_255_255/0.05)]"
          ></div>
          {#each displayLayout.displayKeys as { key, w, h, x, y }, i (i)}
            <div class="absolute p-0.5" style={unitToStyle(w, h, x, y)}>
              {@render keyGenerator?.(key)}
            </div>
          {/each}
        </div>
      </div>
    {/if}
  </div>
</div>
