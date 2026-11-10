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
  import FirmwareTools from "$lib/components/firmware-tools.svelte"
  import FixedScrollArea from "$lib/components/fixed-scroll-area.svelte"
  import { keyboardContext } from "$lib/keyboard"
  import { describeInstalled, updates } from "$lib/updates.svelte"
  import { cn, displayVersion, type WithoutChildren } from "$lib/utils"
  import type { HTMLAttributes } from "svelte/elements"

  const {
    class: className,
    ...props
  }: WithoutChildren<HTMLAttributes<HTMLDivElement>> = $props()

  const keyboard = keyboardContext.get()
</script>

<div
  class={cn("mx-auto flex size-full max-w-3xl flex-col", className)}
  {...props}
>
  <FixedScrollArea class="flex flex-col gap-6 p-4">
    <section class="grid gap-5 rounded-2xl border bg-card p-5 shadow-sm">
      <div class="grid gap-1 text-sm">
        <span class="text-base font-bold">{keyboard.metadata.name}</span>
        <span class="text-muted-foreground">
          <span class="inline-block first-letter:uppercase">
            {updates.installed
              ? `${describeInstalled(updates.installed)} · `
              : ""}libhmk {displayVersion(keyboard.version)}
          </span>
          {keyboard.demo ? "(demo keyboard)" : ""}
        </span>
      </div>
      {#if keyboard.demo}
        <span class="text-sm text-muted-foreground">
          Connect your keyboard to install firmware.
        </span>
      {:else}
        <FirmwareTools enterBootloader={() => keyboard.bootloader()} />
      {/if}
    </section>
  </FixedScrollArea>
</div>
