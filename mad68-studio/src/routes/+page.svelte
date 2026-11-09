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
  import { CableIcon, CpuIcon, ShapesIcon } from "@lucide/svelte"
  import screenshotDark from "$lib/assets/screenshot-dark.png"
  import screenshot from "$lib/assets/screenshot.png"
  import FirmwareTools from "$lib/components/firmware-tools.svelte"
  import FlashOverlay from "$lib/components/flash-overlay.svelte"
  import Footer from "$lib/components/footer.svelte"
  import { Button } from "$lib/components/ui/button"
  import Configurator from "$lib/configurator/configurator.svelte"
  import type { Keyboard } from "$lib/keyboard"
  import { connect } from "$lib/keyboard/hmk-keyboard.svelte"
  import { HMK_FIRMWARE_MAX_VERSION } from "$lib/libhmk"
  import { pendingRestart } from "$lib/restart"
  import { toast } from "svelte-sonner"

  let keyboard: Keyboard | null = $state(null)
  let showFirmware = $state(false)
  let connecting = false

  const handleConnect = async (quiet = false) => {
    if (connecting || keyboard) return
    connecting = true
    try {
      keyboard = await connect(({ metadata: { name } }) => {
        keyboard = null
        if (pendingRestart()) {
          // The app restarted the keyboard to apply a setting
          toast.info(`${name} is restarting...`)
          setTimeout(() => {
            if (!keyboard)
              toast.warning(
                "The keyboard has not come back by itself. Unplug it and plug it back in.",
                { duration: 15000 },
              )
          }, 12000)
        } else {
          toast.success(`${name} disconnected.`)
        }
      })
      if (keyboard) {
        let message = `Successfully connected to ${keyboard.metadata.name}.`
        if (keyboard.version < HMK_FIRMWARE_MAX_VERSION) {
          message += " Newer version of the firmware is available."
        }
        toast.success(message)
      }
    } catch (err) {
      if (!quiet) toast.error(String(err))
    } finally {
      connecting = false
    }
  }

  // The desktop app grants MadLions devices without a prompt, so connect as
  // soon as the keyboard is there (at start-up and whenever it is plugged in).
  const autoConnect = async () => {
    if (!("hid" in navigator) || keyboard) return
    const devices = await navigator.hid.getDevices()
    if (devices.some((d) => d.collections[0]?.usagePage === 0xffab)) {
      await handleConnect(true)
    }
  }

  $effect(() => {
    autoConnect()
    const onConnect = () => setTimeout(autoConnect, 800)
    navigator.hid?.addEventListener("connect", onConnect)
    return () => navigator.hid?.removeEventListener("connect", onConnect)
  })
</script>

{#if keyboard}
  <Configurator {keyboard} />
{:else}
  <main class="py-24">
    <div class="mx-auto max-w-7xl px-6">
      <div class="mx-auto max-w-2xl text-center">
        <h1 class="text-5xl font-semibold tracking-tight">MAD68 Studio</h1>
        <p class="mt-4 text-lg font-medium text-wrap text-muted-foreground">
          Configure your MadLions MAD68 HE: key bindings, actuation points,
          Rapid Trigger, advanced keys, per-key RGB and firmware updates.
        </p>
        <div class="mt-6 flex items-center justify-center gap-4">
          <Button onclick={() => handleConnect()} size="lg">
            <CableIcon /> Connect Keyboard
          </Button>
          <Button href="/demo" size="lg" variant="outline">
            <ShapesIcon /> Try Demo
          </Button>
          <Button
            onclick={() => (showFirmware = !showFirmware)}
            size="lg"
            variant="outline"
          >
            <CpuIcon /> Firmware
          </Button>
        </div>
        {#if showFirmware}
          <div class="mt-8 text-left">
            <FirmwareTools />
          </div>
        {/if}
      </div>
      <div class="mt-16 grid place-items-center">
        <div class="overflow-hidden rounded-xl border shadow-md">
          <img
            class="dark:hidden"
            alt="Screenshot"
            src={screenshot}
            width="1024"
            height="768"
          />
          <img
            class="not-dark:hidden"
            alt="Screenshot"
            src={screenshotDark}
            width="1024"
            height="768"
          />
        </div>
      </div>
    </div>
  </main>
  <Footer />
{/if}

<FlashOverlay />
