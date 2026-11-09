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
  import { Button } from "$lib/components/ui/button"
  import { flashSession } from "$lib/flash-session.svelte"
  import type { FlashProgress } from "$lib/flasher"

  const stageLabel = (p: FlashProgress) =>
    ({
      erase: "Erasing",
      write: "Writing",
      finish: "Finishing",
      done: "Done",
    })[p.stage]

  // Keep the window open while firmware is being written. This lasts only
  // as long as the write: a stalled bootloader times out and releases it.
  $effect(() => {
    if (!(flashSession.busy && flashSession.progress)) return
    const block = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ""
    }
    window.addEventListener("beforeunload", block)
    return () => window.removeEventListener("beforeunload", block)
  })

  const title = $derived(
    flashSession.busy
      ? "Updating firmware"
      : flashSession.result === "success"
        ? "Firmware installed"
        : "Firmware not installed",
  )
</script>

{#if flashSession.visible}
  <div
    class="fixed inset-0 z-50 grid place-items-center bg-background/80 p-6 backdrop-blur-sm"
    role="dialog"
    aria-modal="true"
    aria-labelledby="flash-title"
  >
    <div
      class="grid w-full max-w-md gap-4 rounded-lg border bg-card p-6 shadow-lg"
    >
      <div class="grid gap-1">
        <h2 id="flash-title" class="text-lg font-semibold">{title}</h2>
        <span class="text-sm text-muted-foreground"
          >{flashSession.imageName}</span
        >
      </div>

      <p class="text-sm">{flashSession.status}</p>

      {#if flashSession.progress}
        <div class="grid gap-1">
          <div class="h-2 w-full overflow-hidden rounded-full bg-muted">
            <div
              class="h-full bg-primary transition-[width]"
              style:width={`${Math.round(flashSession.progress.fraction * 100)}%`}
            ></div>
          </div>
          <span class="text-xs text-muted-foreground">
            {stageLabel(flashSession.progress)}
            {Math.round(flashSession.progress.fraction * 100)}%
          </span>
        </div>
      {/if}

      {#if flashSession.busy && flashSession.progress}
        <span class="text-xs font-medium text-destructive">
          Do not unplug the keyboard or close this window.
        </span>
      {/if}

      <div class="flex justify-end gap-2">
        {#if flashSession.busy && !flashSession.progress}
          <Button
            onclick={() => flashSession.cancel()}
            size="sm"
            variant="outline"
          >
            Cancel
          </Button>
        {:else if !flashSession.busy}
          <Button onclick={() => flashSession.dismiss()} size="sm">Close</Button
          >
        {/if}
      </div>
    </div>
  </div>
{/if}
