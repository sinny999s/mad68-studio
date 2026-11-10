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
    ArrowUpCircleIcon,
    FileUpIcon,
    RefreshCwIcon,
    RotateCcwIcon,
    ZapIcon,
  } from "@lucide/svelte"
  import { Button } from "$lib/components/ui/button"
  import * as Dialog from "$lib/components/ui/dialog"
  import { flashSession } from "$lib/flash-session.svelte"
  import {
    detectKeyboardMode,
    displayFirmwareVersion,
    loadBundledFirmware,
    parseFirmware,
    type FirmwareImage,
    type KeyboardMode,
  } from "$lib/flasher"
  import { formatFirmwareRelease } from "$lib/release"
  import {
    BUNDLED_CUSTOM_FIRMWARE,
    describeInstalled,
    updates,
  } from "$lib/updates.svelte"
  import { cn } from "$lib/utils"
  import { toast } from "svelte-sonner"

  const {
    class: className,
    enterBootloader,
  }: {
    class?: string
    // Provided by the configurator when the custom firmware is connected
    enterBootloader?: () => Promise<void>
  } = $props()

  const BUNDLED_STOCK = "/firmware/MAD68HE_RGB_v1.06.bin"

  let mode = $state<KeyboardMode | "checking">("checking")
  let selected: FirmwareImage | null = $state(null)
  let confirmOpen = $state(false)
  const busy = $derived(flashSession.busy)
  let fileInput: HTMLInputElement | null = $state(null)

  const modeLabel: Record<KeyboardMode | "checking", string> = {
    checking: "Checking...",
    custom: "Custom firmware (libhmk)",
    qmk: "QMK firmware",
    stock: "Stock MadLions firmware",
    bootloader: "Update mode (bootloader)",
    none: "No MAD68 HE detected",
  }

  // Which custom firmware this app installs, and whether the connected
  // keyboard (when the configurator has it open) runs an older one
  $effect(() => {
    updates.loadBundled()
  })
  const bundled = $derived(
    updates.bundled ? formatFirmwareRelease(updates.bundled) : null,
  )
  const installed = $derived(
    mode === "custom" && updates.installed
      ? describeInstalled(updates.installed)
      : null,
  )
  const outdated = $derived(mode === "custom" && updates.firmwareOutdated)

  function installBundled() {
    choose(() =>
      loadBundledFirmware(BUNDLED_CUSTOM_FIRMWARE, "MAD68HE_libhmk.bin"),
    )
  }

  async function refresh() {
    mode = "checking"
    mode = await detectKeyboardMode()
  }

  $effect(() => {
    refresh()
    const onChange = () => !busy && refresh()
    navigator.hid?.addEventListener("connect", onChange)
    navigator.hid?.addEventListener("disconnect", onChange)
    return () => {
      navigator.hid?.removeEventListener("connect", onChange)
      navigator.hid?.removeEventListener("disconnect", onChange)
    }
  })

  async function choose(load: () => Promise<FirmwareImage>) {
    try {
      selected = await load()
      confirmOpen = true
    } catch (err) {
      toast.error(String(err instanceof Error ? err.message : err))
    }
  }

  async function onFile(e: Event) {
    const file = (e.target as HTMLInputElement).files?.[0]
    if (!file) return
    await choose(async () =>
      parseFirmware(file.name, new Uint8Array(await file.arrayBuffer())),
    )
    ;(e.target as HTMLInputElement).value = ""
  }

  function run() {
    if (!selected) return
    confirmOpen = false
    // Progress is shown by the app-wide overlay: this page closes when the
    // keyboard drops off USB to enter update mode
    flashSession.run(selected, enterBootloader).finally(refresh)
  }
</script>

<div class={cn("flex flex-col gap-4", className)}>
  <div class="flex items-center justify-between gap-4 rounded-md border p-3">
    <div class="grid text-sm">
      <span class="font-semibold">Keyboard</span>
      <span class="text-muted-foreground first-letter:uppercase">
        {installed ?? modeLabel[mode]}
      </span>
    </div>
    <Button disabled={busy} onclick={refresh} size="sm" variant="outline">
      <RefreshCwIcon /> Refresh
    </Button>
  </div>

  {#if outdated && bundled}
    <div
      class="flex flex-wrap items-center justify-between gap-3 rounded-md border border-primary/50 bg-primary/10 p-3"
    >
      <div class="grid text-sm">
        <span class="font-semibold">Firmware update available</span>
        <span class="text-muted-foreground">
          Your keyboard has {installed}. This app has {bundled}, and updating
          keeps your settings.
        </span>
      </div>
      <Button disabled={busy} onclick={installBundled} size="sm">
        <ArrowUpCircleIcon /> Update to {bundled}
      </Button>
    </div>
  {/if}

  <div class="grid gap-2 text-sm">
    <span class="font-semibold">Install firmware</span>
    <span class="text-muted-foreground">
      Uses the keyboard's own update mode, the same way the official MadLions
      tool does. The bootloader and your stock settings are never touched, so
      you can always come back to stock.
    </span>
  </div>

  <div class="flex flex-wrap gap-2">
    <Button
      disabled={busy}
      onclick={installBundled}
      size="sm"
      variant={outdated ? "outline" : "default"}
    >
      <ZapIcon /> Install custom firmware{bundled ? ` ${bundled}` : ""}
    </Button>
    <Button
      disabled={busy}
      onclick={() =>
        choose(() =>
          loadBundledFirmware(BUNDLED_STOCK, "MAD68HE_RGB_v1.06.bin"),
        )}
      size="sm"
      variant="outline"
    >
      <RotateCcwIcon /> Restore stock firmware (v1.06)
    </Button>
    <Button
      disabled={busy}
      onclick={() => fileInput?.click()}
      size="sm"
      variant="outline"
    >
      <FileUpIcon /> Install from file...
    </Button>
    <input
      bind:this={fileInput}
      accept=".bin"
      class="hidden"
      onchange={onFile}
      type="file"
    />
  </div>
</div>

<Dialog.Root bind:open={confirmOpen}>
  <Dialog.Content>
    <Dialog.Header>
      <Dialog.Title>Install {selected?.name}?</Dialog.Title>
      <Dialog.Description>
        {#if selected}
          {selected.kind === "custom"
            ? `Custom libhmk firmware${selected.release ? ` ${formatFirmwareRelease(selected.release)}` : ""}`
            : selected.kind === "qmk"
              ? "QMK firmware"
              : `Stock MadLions firmware ${displayFirmwareVersion(selected.version)}`},
          {Math.round(selected.bodyLength / 1024)} KB. The keyboard will stop typing
          for about half a minute while it updates. Do not unplug it.
        {/if}
      </Dialog.Description>
    </Dialog.Header>
    <Dialog.Footer>
      <Dialog.Close>
        {#snippet child({ props })}
          <Button size="sm" variant="outline" {...props}>Cancel</Button>
        {/snippet}
      </Dialog.Close>
      <Button onclick={run} size="sm">Install</Button>
    </Dialog.Footer>
  </Dialog.Content>
</Dialog.Root>
