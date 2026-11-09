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
Live key travel inside a keycap: a fill that rises as the key goes down, and
optionally a line at the actuation point. The fill brightens once the key has
passed it. Sits behind the keycap's legends (the key button is its own
stacking context).
-->
<script lang="ts">
  import { HMK_MAX_DISTANCE } from "$lib/libhmk"
  import { cn } from "$lib/utils"

  const {
    distance = 0,
    actuation,
    class: className,
  }: {
    // 0 at rest to HMK_MAX_DISTANCE at full travel
    distance?: number
    actuation?: number
    class?: string
  } = $props()

  const fraction = (v: number) => Math.max(0, Math.min(1, v / HMK_MAX_DISTANCE))

  const depth = $derived(fraction(distance))
  const reached = $derived(
    actuation !== undefined && distance > 0 && distance >= actuation,
  )
</script>

<span
  aria-hidden="true"
  class={cn(
    "pointer-events-none absolute inset-0 -z-10 overflow-hidden rounded-[inherit]",
    className,
  )}
>
  <span
    class={cn(
      "absolute inset-x-0 bottom-0 bg-linear-to-t transition-[height] duration-75 ease-out",
      reached
        ? "from-primary/70 to-primary/40"
        : "from-primary/40 to-primary/10",
    )}
    style:height="{depth * 100}%"
  ></span>
  {#if actuation !== undefined}
    <!-- Actuation point: a notch on each side, clear of the legend -->
    {#each ["left-0 rounded-r-full", "right-0 rounded-l-full"] as side (side)}
      <span
        class={cn("absolute h-[0.16em] w-[16%] bg-primary", side)}
        style:bottom="calc({fraction(actuation) * 100}% - 0.08em)"
      ></span>
    {/each}
  {/if}
</span>
