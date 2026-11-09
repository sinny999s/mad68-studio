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
  import { Input } from "$lib/components/ui/input"
  import {
    css,
    hsvToRgb,
    parseHex,
    RAINBOW_GRADIENT,
    rgbToHsv,
    toHex,
  } from "$lib/lighting/color"
  import { SWATCHES } from "$lib/lighting/effects"
  import { cn } from "$lib/utils"

  const {
    hue,
    sat,
    value = 255,
    showValue = false,
    title,
    description,
    class: className,
    onchange,
  }: {
    hue: number
    sat: number
    value?: number
    showValue?: boolean
    title: string
    description?: string
    class?: string
    // `done` is false while dragging (live preview) and true on release
    onchange: (h: number, s: number, v: number, done: boolean) => void
  } = $props()

  let plane: HTMLDivElement | null = $state(null)
  let dragging = false
  let hexText = $state("")
  let hexFocused = false

  const color = $derived(hsvToRgb(hue, sat, value))
  $effect(() => {
    const hex = toHex(color)
    if (!hexFocused) hexText = hex
  })

  const clamp = (v: number) => Math.max(0, Math.min(1, v))

  function pick(e: PointerEvent, done: boolean) {
    if (!plane) return
    const r = plane.getBoundingClientRect()
    const x = clamp((e.clientX - r.left) / r.width)
    const y = clamp((e.clientY - r.top) / r.height)
    onchange(Math.round(x * 255), Math.round((1 - y) * 255), value, done)
  }

  function commitHex() {
    const rgb = parseHex(hexText)
    if (!rgb) {
      hexText = toHex(color)
      return
    }
    const [h, s, v] = rgbToHsv(rgb)
    onchange(h, s, showValue ? v : value, true)
  }
</script>

<div class={cn("grid gap-2", className)}>
  <div class="flex items-center gap-3">
    <div
      class="size-9 shrink-0 rounded-md border shadow-inner"
      style:background={css(color)}
    ></div>
    <div class="grid flex-1 text-sm">
      <span class="font-medium">{title}</span>
      {#if description}
        <span class="text-xs text-muted-foreground">{description}</span>
      {/if}
    </div>
    <Input
      bind:value={hexText}
      class="h-8 w-24 font-mono text-xs"
      maxlength={7}
      onblur={() => {
        hexFocused = false
        commitHex()
      }}
      onfocus={() => (hexFocused = true)}
      onkeydown={(e) => e.key === "Enter" && commitHex()}
      spellcheck={false}
    />
  </div>

  <div
    bind:this={plane}
    class="relative h-24 cursor-crosshair touch-none rounded-md border"
    style:background="linear-gradient(to top, #fff, rgb(255 255 255 / 0)), {RAINBOW_GRADIENT}"
    onpointerdown={(e) => {
      plane?.setPointerCapture(e.pointerId)
      dragging = true
      pick(e, false)
    }}
    onpointermove={(e) => dragging && pick(e, false)}
    onpointerup={(e) => {
      if (!dragging) return
      dragging = false
      pick(e, true)
    }}
    role="slider"
    aria-label="{title}: hue and saturation"
    aria-valuenow={hue}
    tabindex="0"
    onkeydown={(e) => {
      const step = e.shiftKey ? 16 : 4
      if (e.key === "ArrowLeft") onchange((hue - step) & 0xff, sat, value, true)
      else if (e.key === "ArrowRight")
        onchange((hue + step) & 0xff, sat, value, true)
      else if (e.key === "ArrowUp")
        onchange(hue, Math.min(255, sat + step), value, true)
      else if (e.key === "ArrowDown")
        onchange(hue, Math.max(0, sat - step), value, true)
    }}
  >
    <div
      class="pointer-events-none absolute size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white shadow-md ring-1 ring-black/40"
      style:left="{(hue / 255) * 100}%"
      style:top="{(1 - sat / 255) * 100}%"
      style:background={css(hsvToRgb(hue, sat, 255))}
    ></div>
  </div>

  {#if showValue}
    <label class="grid gap-1 text-xs text-muted-foreground">
      <span>Shade: {Math.round((value / 255) * 100)}%</span>
      <input
        class="h-2 w-full cursor-pointer appearance-none rounded-full border"
        style:background="linear-gradient(to right, #000, {css(
          hsvToRgb(hue, sat, 255),
        )})"
        type="range"
        min="0"
        max="255"
        {value}
        oninput={(e) =>
          onchange(hue, sat, Number(e.currentTarget.value), false)}
        onchange={(e) =>
          onchange(hue, sat, Number(e.currentTarget.value), true)}
      />
    </label>
  {/if}

  <div class="flex flex-wrap gap-1.5">
    {#each SWATCHES as [name, h, s] (name)}
      <button
        class={cn(
          "size-6 rounded-full border shadow-sm transition-transform hover:scale-110",
          h === hue &&
            s === sat &&
            "ring-2 ring-ring ring-offset-1 ring-offset-background",
        )}
        style:background={css(hsvToRgb(h, s, 255))}
        title={name}
        aria-label={name}
        onclick={() => onchange(h, s, value, true)}
      ></button>
    {/each}
  </div>
</div>
