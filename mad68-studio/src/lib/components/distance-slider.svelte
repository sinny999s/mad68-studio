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
    displayUnitDistance,
    distanceToUnit,
    switchDistanceUnit,
    unitToDistance,
  } from "$lib/distance"
  import { optMap } from "$lib/utils"
  import type { ComponentProps } from "svelte"
  import CommitSlider from "./commit-slider.svelte"

  let {
    committed = $bindable(),
    min = 1,
    max,
    onCommit,
    ...props
  }: ComponentProps<typeof CommitSlider> = $props()

  // Full travel unless the page limits it; follows Settings > Switch Travel
  const top = $derived(max ?? switchDistanceUnit())
</script>

<CommitSlider
  bind:committed={
    () => optMap(committed, distanceToUnit),
    (v) => (committed = optMap(v, unitToDistance))
  }
  display={(v) => `${displayUnitDistance(v)}mm`}
  {min}
  max={top}
  onCommit={(v) => onCommit?.(unitToDistance(v))}
  {...props}
/>
