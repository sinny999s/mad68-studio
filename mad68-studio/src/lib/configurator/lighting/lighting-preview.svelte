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
  import { KeyboardEditorKeyboard } from "$lib/components/keyboard-editor"
  import { keyboardContext } from "$lib/keyboard"
  import { getKeycodeMetadata } from "$lib/keycodes"
  import type { RgbConfig } from "$lib/libhmk/commands/rgb"
  import { css, scale, type RGB } from "$lib/lighting/color"
  import { ledKeysFromLayout, LightingEngine } from "$lib/lighting/engine"
  import { cn } from "$lib/utils"
  import { displayLayoutContext } from "../context.svelte"

  const {
    config,
    maxBrightness,
    keyColors,
    keyCount,
    baseLayer,
    liveKeys = true,
    painting = false,
    onpaint,
    class: className,
  }: {
    config: RgbConfig
    maxBrightness: number
    keyColors: RGB[]
    keyCount: number
    baseLayer?: number[]
    liveKeys?: boolean
    painting?: boolean
    onpaint?: (key: number, done: boolean) => void
    class?: string
  } = $props()

  const keyboard = keyboardContext.get()
  const displayLayout = displayLayoutContext.get()
  const ledKeys = ledKeysFromLayout(displayLayout.displayKeys)
  const engine = $derived(new LightingEngine(ledKeys, keyCount))

  // Colour of each key, by key index, refreshed every animation frame
  let colors = $state<RGB[]>([])
  let mouseKey: number | null = null
  let strokeActive = false

  $effect(() => {
    let raf = 0
    const tick = (t: number) => {
      if (engine.update(t, config)) {
        const next: RGB[] = Array.from({ length: keyCount }, () => [0, 0, 0])
        for (const led of ledKeys) next[led.key] = engine.color(led, keyColors)
        colors = next
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  })

  // Mirror real key presses (travel 0-255) for the typing-driven effects
  $effect(() => {
    if (!liveKeys || keyboard.demo) return
    let stopped = false
    let busy = false
    const timer = setInterval(async () => {
      if (busy || document.hidden) return
      busy = true
      try {
        const info = await keyboard.analogInfo()
        if (stopped) return
        info.forEach(({ distance }, k) => {
          if (k !== mouseKey) engine.setKey(k, distance)
        })
      } catch {
        // keyboard busy or gone: skip this sample
      } finally {
        busy = false
      }
    }, 90)
    return () => {
      stopped = true
      clearInterval(timer)
    }
  })

  // On-screen brightness: dimmer settings look dimmer, but never invisible
  const shown = (c: RGB) =>
    config.on
      ? scale(
          c,
          70 +
            Math.round((185 * config.brightness) / Math.max(1, maxBrightness)),
        )
      : ([14, 14, 16] as RGB)

  const label = (key: number) => {
    const kc = baseLayer?.[key]
    if (kc === undefined) return ""
    const { name, display } = getKeycodeMetadata(kc)
    const text = (display ?? [name]).find((d) => typeof d === "string")
    return typeof text === "string" ? text : ""
  }

  const ink = ([r, g, b]: RGB) =>
    r * 0.299 + g * 0.587 + b * 0.114 > 150
      ? "rgb(10 10 12 / 0.85)"
      : "rgb(255 255 255 / 0.85)"

  function press(key: number) {
    if (painting) {
      strokeActive = true
      onpaint?.(key, false)
      return
    }
    mouseKey = key
    engine.setKey(key, 255, true)
  }

  function release() {
    if (painting) {
      if (strokeActive) onpaint?.(-1, true)
      strokeActive = false
      return
    }
    if (mouseKey !== null) engine.setKey(mouseKey, 0, false)
    mouseKey = null
  }
</script>

<svelte:window onpointerup={release} />

<div
  class={cn(
    "relative h-56 overflow-hidden rounded-xl border bg-zinc-950 md:h-64",
    painting && "cursor-crosshair",
    className,
  )}
>
  <KeyboardEditorKeyboard class="h-full p-3">
    {#snippet keyGenerator(key)}
      {@const c = shown(colors[key] ?? [0, 0, 0])}
      <button
        class="relative size-full overflow-hidden rounded-[0.35em] border border-white/10 transition-[transform] duration-75 active:scale-95"
        style:background={css(c)}
        style:box-shadow="0 0 0.9em {css(c, 0.45)}, inset 0 -0.18em 0 rgb(0 0 0
        / 0.35)"
        aria-label="Key {key + 1}: {label(key) || 'key'}"
        onpointerdown={(e) => {
          e.preventDefault()
          press(key)
        }}
        onpointerenter={(e) => {
          if (painting && strokeActive && e.buttons & 1) onpaint?.(key, false)
        }}
        onpointerleave={() => {
          if (!painting && mouseKey === key) release()
        }}
      >
        <span
          class="pointer-events-none absolute top-[0.2em] left-[0.35em] text-[0.5em] leading-none font-medium"
          style:color={ink(c)}
        >
          {label(key)}
        </span>
      </button>
    {/snippet}
  </KeyboardEditorKeyboard>
</div>
