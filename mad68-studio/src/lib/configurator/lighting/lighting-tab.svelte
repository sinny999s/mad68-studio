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
    CpuIcon,
    PaintbrushIcon,
    PaintBucketIcon,
    PipetteIcon,
    SaveIcon,
    Trash2Icon,
    XIcon,
  } from "@lucide/svelte"
  import CommitSlider from "$lib/components/commit-slider.svelte"
  import FixedScrollArea from "$lib/components/fixed-scroll-area.svelte"
  import Switch from "$lib/components/switch.svelte"
  import { Button } from "$lib/components/ui/button"
  import { Input } from "$lib/components/ui/input"
  import * as Select from "$lib/components/ui/select"
  import { keyboardContext } from "$lib/keyboard"
  import {
    RGB_IND_CAPS,
    RGB_IND_LAYER,
    RGB_IND_PROFILE,
    type RgbConfig,
    type RgbInfo,
  } from "$lib/libhmk/commands/rgb"
  import {
    css,
    hsvToRgb,
    RAINBOW_GRADIENT,
    rgbToHsv,
    scale,
    type RGB,
  } from "$lib/lighting/color"
  import {
    DIRECTIONS,
    Effect,
    EFFECTS,
    IDLE_OPTIONS,
    KEY_GROUPS,
    PRESETS,
    type LightingPreset,
  } from "$lib/lighting/effects"
  import { cn, type WithoutChildren } from "$lib/utils"
  import { toast } from "svelte-sonner"
  import type { HTMLAttributes } from "svelte/elements"
  import { globalStateContext } from "../context.svelte"
  import { keymapQueryContext } from "../queries/keymap-query.svelte"
  import ColorPicker from "./color-picker.svelte"
  import LightingPreview from "./lighting-preview.svelte"

  const {
    class: className,
    ...props
  }: WithoutChildren<HTMLAttributes<HTMLDivElement>> = $props()

  const keyboard = keyboardContext.get()
  const globalState = globalStateContext.get()
  const keymapQuery = keymapQueryContext.get()
  const { current: keymap } = $derived(keymapQuery.keymap)
  const baseLayer = $derived(keymap?.[0])

  let info = $state<RgbInfo | null>(null)
  let keyColors = $state<RGB[]>([])
  let unsupported = $state(false)

  $effect(() => {
    keyboard
      .getRgb()
      .then(async (i) => {
        info = i
        if (i.version >= 2) keyColors = await keyboard.getRgbKeys()
      })
      .catch(() => (unsupported = true))
  })

  const v2 = $derived((info?.version ?? 1) >= 2)
  const keyCount = $derived(info?.keyCount || keyboard.metadata.numKeys)
  const fx = $derived(info ? EFFECTS[info.mode] : undefined)

  //--------------------------------------------------------------------+
  // Writes: one in flight at a time, always ending with the latest state
  //--------------------------------------------------------------------+

  const configOf = (i: RgbInfo): RgbConfig => ({
    on: i.on,
    mode: i.mode,
    hue: i.hue,
    sat: i.sat,
    brightness: i.brightness,
    speed: i.speed,
    hue2: i.hue2,
    sat2: i.sat2,
    direction: i.direction,
    idleMinutes: i.idleMinutes,
    indicators: i.indicators,
    indicatorHue: i.indicatorHue,
    indicatorSat: i.indicatorSat,
  })

  let configQueued: RgbConfig | null = null
  let configSending = false

  async function sendConfig() {
    if (configSending) return
    configSending = true
    try {
      while (configQueued) {
        const next = configQueued
        configQueued = null
        try {
          await keyboard.setRgb({ data: next })
        } catch (err) {
          toast.error(`Could not update lighting: ${err}`)
        }
      }
    } finally {
      configSending = false
    }
  }

  function update(patch: Partial<RgbConfig>) {
    if (!info) return
    info = { ...info, ...patch }
    configQueued = configOf(info)
    sendConfig()
  }

  let keysQueued = false
  let keysSending = false

  async function sendKeys() {
    if (keysSending) return
    keysSending = true
    try {
      while (keysQueued) {
        keysQueued = false
        try {
          await keyboard.setRgbKeys({
            offset: 0,
            data: keyColors.map((c) => [...c] as RGB),
          })
        } catch (err) {
          toast.error(`Could not update key colours: ${err}`)
        }
      }
    } finally {
      keysSending = false
    }
  }

  function setKeys(next: RGB[]) {
    keyColors = next
    keysQueued = true
    sendKeys()
    if (info && info.mode !== Effect.Custom) update({ mode: Effect.Custom })
  }

  //--------------------------------------------------------------------+
  // Painting
  //--------------------------------------------------------------------+

  let brush = $state({ h: 0, s: 255, v: 255 })
  let tool: "paint" | "pick" = $state("paint")
  const brushRgb = $derived(hsvToRgb(brush.h, brush.s, brush.v))

  function onpaint(key: number) {
    if (key < 0) return
    if (tool === "pick") {
      const [h, s, v] = rgbToHsv(keyColors[key] ?? [0, 0, 0])
      brush = { h, s, v }
      tool = "paint"
      return
    }
    const current = keyColors[key]
    if (current && current.every((c, i) => c === brushRgb[i])) return
    const next = [...keyColors]
    next[key] = [...brushRgb] as RGB
    setKeys(next)
  }

  function fill(keys: number[] | "all", color: RGB = brushRgb) {
    const next = [...keyColors]
    for (let k = 0; k < keyCount; k++)
      if (keys === "all" || keys.includes(k)) next[k] = [...color] as RGB
    setKeys(next)
  }

  const keysWith = (codes: number[]) =>
    baseLayer
      ? baseLayer.flatMap((kc, k) => (codes.includes(kc) ? [k] : []))
      : []

  //--------------------------------------------------------------------+
  // Presets
  //--------------------------------------------------------------------+

  type SavedPreset = { name: string; config: Partial<RgbConfig>; keys?: RGB[] }
  const STORE = "mad68-studio.lighting-presets"

  function loadSaved(): SavedPreset[] {
    try {
      return JSON.parse(localStorage.getItem(STORE) ?? "[]")
    } catch {
      return []
    }
  }
  let saved = $state<SavedPreset[]>(loadSaved())
  let presetName = $state("")

  function storeSaved(list: SavedPreset[]) {
    saved = list
    try {
      localStorage.setItem(STORE, JSON.stringify(list))
    } catch {
      toast.error("Could not save presets on this computer.")
    }
  }

  function applyPreset(p: LightingPreset | SavedPreset) {
    if (!info) return
    const keys = typeof p.keys === "function" ? p.keys(baseLayer ?? []) : p.keys
    if (keys && v2) {
      keyColors = keys.map((c) => [...c] as RGB)
      keysQueued = true
      sendKeys()
    }
    const allowed =
      p.config.mode === undefined || p.config.mode < info.modeCount
    // Presets switch the lighting on, unless the preset is about switching it off
    update({
      ...p.config,
      on: p.config.on ?? true,
      ...(allowed ? {} : { mode: 0 }),
    })
  }

  function savePreset() {
    if (!info) return
    const name = presetName.trim() || `Preset ${saved.length + 1}`
    const { mode, hue, sat, hue2, sat2, speed, direction, brightness } = info
    storeSaved([
      ...saved.filter((p) => p.name !== name),
      {
        name,
        config: { mode, hue, sat, hue2, sat2, speed, direction, brightness },
        keys:
          mode === Effect.Custom
            ? keyColors.map((c) => [...c] as RGB)
            : undefined,
      },
    ])
    presetName = ""
    toast.success(`Saved "${name}".`)
  }

  //--------------------------------------------------------------------+
  // Looks
  //--------------------------------------------------------------------+

  const percent = (v: number, max: number) =>
    `${Math.round((v * 100) / Math.max(max, 1))}%`

  function swatch(mode: number, i: RgbInfo): string {
    const c1 = css(hsvToRgb(i.hue, i.sat, 255))
    const c2 = css(hsvToRgb(i.hue2, i.sat2, 255))
    const dim1 = css(scale(hsvToRgb(i.hue, i.sat, 255), 50))
    const dim2 = css(scale(hsvToRgb(i.hue2, i.sat2, 255), 70))
    switch (mode) {
      case Effect.Breathing:
        return `linear-gradient(90deg, ${c1}, #000 50%, ${c1})`
      case Effect.RainbowWave:
        return RAINBOW_GRADIENT
      case Effect.RainbowCycle:
        return `conic-gradient(${[0, 43, 85, 128, 170, 213, 255].map((h) => css(hsvToRgb(h, 255, 255))).join(", ")})`
      case Effect.Reactive:
        return `radial-gradient(circle at 35% 55%, ${c2} 0 16%, ${dim1} 42%)`
      case Effect.Custom:
        return keyColors.length
          ? `linear-gradient(90deg, ${[0, 8, 17, 25, 33, 41, 50, 58]
              .map((k) => css(keyColors[k % keyColors.length] ?? [0, 0, 0]))
              .join(", ")})`
          : RAINBOW_GRADIENT
      case Effect.KeyDepth:
        return `linear-gradient(to top, ${c2}, ${dim1})`
      case Effect.Ripple:
        return `repeating-radial-gradient(circle at 30% 50%, ${c2} 0 3px, ${dim1} 3px 9px)`
      case Effect.Splash:
        return `repeating-radial-gradient(circle at 35% 50%, ${css(hsvToRgb(0, 255, 255))} 0 3px, #000 3px 6px, ${css(hsvToRgb(85, 255, 255))} 6px 9px, #000 9px 12px, ${css(hsvToRgb(170, 255, 255))} 12px 15px, #000 15px 18px)`
      case Effect.Starlight:
        return `radial-gradient(circle at 20% 30%, ${c2} 0 2px, transparent 3px), radial-gradient(circle at 62% 70%, ${c2} 0 2px, transparent 3px), radial-gradient(circle at 85% 25%, ${c2} 0 2px, transparent 3px), ${dim1}`
      case Effect.Raindrops:
        return `radial-gradient(circle at 18% 35%, ${css(hsvToRgb(10, 255, 255))} 0 3px, transparent 4px), radial-gradient(circle at 50% 70%, ${css(hsvToRgb(140, 255, 255))} 0 3px, transparent 4px), radial-gradient(circle at 80% 30%, ${css(hsvToRgb(210, 255, 255))} 0 3px, transparent 4px), #111`
      case Effect.Gradient:
        return `linear-gradient(90deg, ${c1}, ${c2})`
      case Effect.Heatmap:
        return `linear-gradient(90deg, ${css(hsvToRgb(170, 255, 120))}, ${css(hsvToRgb(85, 255, 255))}, ${css(hsvToRgb(40, 255, 255))}, ${css(hsvToRgb(0, 255, 255))})`
      case Effect.DualBreathing:
        return `linear-gradient(90deg, ${c1}, ${c2}, ${c1})`
      case Effect.Wave:
        return `linear-gradient(90deg, ${dim2} 0 25%, ${c1} 50%, ${dim2} 75%)`
      case Effect.Fire:
        return `linear-gradient(to top, ${css(hsvToRgb((i.hue + 40) & 0xff, 150, 255))}, ${css(hsvToRgb((i.hue + 20) & 0xff, 255, 255))} 35%, ${css(hsvToRgb(i.hue, 255, 170))} 65%, #000)`
      case Effect.DigitalRain:
        return `linear-gradient(to bottom, #000 5%, transparent 95%), repeating-linear-gradient(90deg, ${c1} 0 3px, #000 3px 9px)`
      case Effect.Spiral:
        return `repeating-radial-gradient(circle at 45% 50%, transparent 0 6px, rgb(0 0 0 / 0.3) 6px 9px), conic-gradient(from 45deg at 45% 50%, ${[0, 43, 85, 128, 170, 213, 255].map((h) => css(hsvToRgb((i.hue + h) & 0xff, i.sat, 255))).join(", ")})`
      case Effect.ReactiveCross:
        return `linear-gradient(${c2}, ${c2}) 0 50% / 100% 3px no-repeat, linear-gradient(${c2}, ${c2}) 35% 0 / 3px 100% no-repeat, ${dim1}`
      case Effect.Solid:
      default:
        return c1
    }
  }

  const sectionTitle = "text-sm font-semibold"
  const card = "grid gap-4 rounded-lg border p-4"
</script>

<div
  class={cn("mx-auto flex size-full max-w-5xl flex-col", className)}
  {...props}
>
  <FixedScrollArea class="flex flex-col gap-6 p-4">
    {#if unsupported}
      <div class="grid gap-1 text-sm">
        <span class="font-semibold">Lighting not available</span>
        <span class="text-muted-foreground">
          This keyboard's firmware does not support lighting control. Install
          the latest MAD68 HE firmware from the Firmware page.
        </span>
      </div>
    {:else if !info}
      <span class="text-sm text-muted-foreground">Reading lighting...</span>
    {:else}
      {#if !v2}
        <div
          class="flex flex-wrap items-center gap-3 rounded-lg border border-primary/40 bg-primary/5 p-3 text-sm"
        >
          <div class="grid flex-1">
            <span class="font-semibold">More lighting with firmware v5</span>
            <span class="text-muted-foreground">
              10 more effects, a second colour, per-key painting, Caps Lock and
              Fn indicators, and idle dimming.
            </span>
          </div>
          <Button onclick={() => (globalState.tab = "firmware")} size="sm">
            <CpuIcon /> Update firmware
          </Button>
        </div>
      {:else if info.modeCount < EFFECTS.length}
        <div
          class="flex flex-wrap items-center gap-3 rounded-lg border border-primary/40 bg-primary/5 p-3 text-sm"
        >
          <div class="grid flex-1">
            <span class="font-semibold">
              {EFFECTS.length - info.modeCount} more effects with the latest firmware
            </span>
            <span class="text-muted-foreground">
              {EFFECTS.slice(info.modeCount)
                .map((e) => e.name)
                .join(", ")}. Ripple and Splash also grow with how hard you
              press.
            </span>
          </div>
          <Button onclick={() => (globalState.tab = "firmware")} size="sm">
            <CpuIcon /> Update firmware
          </Button>
        </div>
      {/if}

      <div class="flex flex-wrap items-end gap-6">
        <Switch
          checked={info.on}
          onCheckedChange={(v) => update({ on: v })}
          id="rgb-on"
          title="Lighting"
          description="Fn + Tab on the keyboard"
        />
        <CommitSlider
          class="min-w-64 flex-1"
          committed={info.brightness}
          display={(v) => percent(v, info?.maxBrightness ?? 210)}
          max={info.maxBrightness}
          min={0}
          onCommit={(v) => update({ brightness: v, on: true })}
          step={1}
          title="Brightness"
          description="Capped at the stock maximum. Fn + Q / Fn + A."
        />
      </div>

      <div class="grid gap-2">
        <LightingPreview
          config={configOf(info)}
          maxBrightness={info.maxBrightness}
          {keyColors}
          {keyCount}
          {baseLayer}
          painting={v2 && info.mode === Effect.Custom}
          {onpaint}
        />
        <span class="text-xs text-muted-foreground">
          {#if v2 && info.mode === Effect.Custom}
            {tool === "pick"
              ? "Click a key to pick up its colour."
              : "Click or drag across keys to paint them with the brush colour."}
          {:else if fx?.keys}
            Type on your keyboard or click keys here to see the effect.
          {:else}
            Live preview of the lighting on your keyboard.
          {/if}
        </span>
      </div>

      <section class="grid gap-3">
        <div class="flex items-baseline justify-between">
          <span class={sectionTitle}>Effect</span>
          <span class="text-xs text-muted-foreground">
            Fn + W / Fn + S on the keyboard
          </span>
        </div>
        <div class="grid grid-cols-3 gap-2 sm:grid-cols-5">
          {#each EFFECTS.slice(0, info.modeCount) as e, i (e.name)}
            <button
              class={cn(
                "grid gap-1.5 rounded-lg border p-1.5 text-left text-xs transition-colors hover:bg-muted",
                info.mode === i &&
                  "border-primary bg-muted ring-1 ring-primary",
              )}
              title={e.description}
              onclick={() => update({ mode: i, on: true })}
            >
              <div
                class="h-8 rounded-md border border-white/10"
                style:background={swatch(i, info)}
              ></div>
              <span class="truncate px-0.5 font-medium">{e.name}</span>
            </button>
          {/each}
        </div>
        {#if fx}
          <span class="text-sm text-muted-foreground">{fx.description}</span>
        {/if}
      </section>

      {#if fx}
        <section class="grid gap-6 md:grid-cols-2">
          {#if fx.color}
            <ColorPicker
              hue={info.hue}
              sat={info.sat}
              title={fx.color2 && v2 ? "Colour 1" : "Colour"}
              description="Fn + E / Fn + D change the hue"
              onchange={(h, s) => update({ hue: h, sat: s })}
            />
          {/if}
          {#if fx.color2 && v2}
            <ColorPicker
              hue={info.hue2}
              sat={info.sat2}
              title="Colour 2"
              description={info.mode === Effect.KeyDepth
                ? "Colour at full key travel"
                : "Accent colour"}
              onchange={(h, s) => update({ hue2: h, sat2: s })}
            />
          {/if}
          {#if fx.speed || (fx.direction && v2)}
            <div class="grid content-start gap-4">
              {#if fx.speed}
                <CommitSlider
                  committed={info.speed}
                  display={(v) => percent(v, 255)}
                  max={255}
                  min={0}
                  onCommit={(v) => update({ speed: v })}
                  step={1}
                  title="Speed"
                  description="Fn + R / Fn + F on the keyboard"
                />
              {/if}
              {#if fx.direction && v2}
                <div class="grid gap-2 text-sm">
                  <span class="font-bold">Direction</span>
                  <Select.Root
                    bind:value={
                      () => String(info?.direction ?? 0),
                      (v) => update({ direction: Number(v) })
                    }
                    type="single"
                  >
                    <Select.Trigger class="w-56" size="sm">
                      {DIRECTIONS[info.direction] ?? "Left to right"}
                    </Select.Trigger>
                    <Select.Content>
                      {#each DIRECTIONS.slice(0, info.directionCount) as d, i (d)}
                        <Select.Item value={String(i)}>{d}</Select.Item>
                      {/each}
                    </Select.Content>
                  </Select.Root>
                </div>
              {/if}
            </div>
          {/if}
        </section>
      {/if}

      {#if v2 && info.mode === Effect.Custom}
        <section class={card}>
          <div class="flex flex-wrap items-center justify-between gap-2">
            <span class={sectionTitle}>Paint keys</span>
            <div class="flex gap-1">
              <Button
                onclick={() => (tool = "paint")}
                size="sm"
                variant={tool === "paint" ? "default" : "outline"}
              >
                <PaintbrushIcon /> Brush
              </Button>
              <Button
                onclick={() => (tool = "pick")}
                size="sm"
                variant={tool === "pick" ? "default" : "outline"}
              >
                <PipetteIcon /> Pick colour
              </Button>
            </div>
          </div>
          <div class="grid gap-6 md:grid-cols-2">
            <ColorPicker
              hue={brush.h}
              sat={brush.s}
              value={brush.v}
              showValue
              title="Brush colour"
              onchange={(h, s, v) => {
                brush = { h, s, v }
                tool = "paint"
              }}
            />
            <div class="grid content-start gap-3">
              <span class="text-sm font-bold">Fill with the brush colour</span>
              <div class="flex flex-wrap gap-2">
                <Button onclick={() => fill("all")} size="sm" variant="outline">
                  <PaintBucketIcon /> All keys
                </Button>
                {#each KEY_GROUPS as g (g.name)}
                  <Button
                    disabled={!baseLayer}
                    onclick={() => fill(keysWith(g.keycodes))}
                    size="sm"
                    variant="outline"
                  >
                    {g.name}
                  </Button>
                {/each}
              </div>
              <div>
                <Button
                  onclick={() => fill("all", [0, 0, 0])}
                  size="sm"
                  variant="ghost"
                >
                  <XIcon /> Turn all keys off
                </Button>
              </div>
              <span class="text-xs text-muted-foreground">
                Key colours are stored on the keyboard, separately from the
                effect, so you can switch effects and come back to them.
              </span>
            </div>
          </div>
        </section>
      {/if}

      <section class={card}>
        <span class={sectionTitle}>Presets</span>
        <div class="flex flex-wrap gap-2">
          {#each PRESETS.filter((p) => v2 || (p.config.mode ?? 0) < info!.modeCount) as p (p.name)}
            <Button
              onclick={() => applyPreset(p)}
              size="sm"
              title={p.description}
              variant="outline"
            >
              {p.name}
            </Button>
          {/each}
        </div>
        {#if saved.length}
          <div class="flex flex-wrap gap-2">
            {#each saved as p (p.name)}
              <div class="flex items-center rounded-md border">
                <Button
                  class="rounded-r-none"
                  onclick={() => applyPreset(p)}
                  size="sm"
                  variant="ghost"
                >
                  {p.name}
                </Button>
                <Button
                  aria-label="Delete {p.name}"
                  class="rounded-l-none px-2"
                  onclick={() => storeSaved(saved.filter((x) => x !== p))}
                  size="sm"
                  variant="ghost"
                >
                  <Trash2Icon />
                </Button>
              </div>
            {/each}
          </div>
        {/if}
        <form
          class="flex gap-2"
          onsubmit={(e) => {
            e.preventDefault()
            savePreset()
          }}
        >
          <Input
            bind:value={presetName}
            class="h-8 max-w-56"
            placeholder="Name this look"
          />
          <Button size="sm" type="submit" variant="secondary">
            <SaveIcon /> Save current
          </Button>
        </form>
      </section>

      {#if v2}
        <section class="grid gap-6 md:grid-cols-2">
          <div class={card}>
            <span class={sectionTitle}>Indicators</span>
            <Switch
              checked={(info.indicators & RGB_IND_CAPS) !== 0}
              onCheckedChange={(v) =>
                update({
                  indicators: v
                    ? info!.indicators | RGB_IND_CAPS
                    : info!.indicators & ~RGB_IND_CAPS,
                })}
              id="ind-caps"
              title="Caps Lock"
              description="Caps key lights up while Caps Lock is on (even with lighting off)"
            />
            <Switch
              checked={(info.indicators & RGB_IND_LAYER) !== 0}
              onCheckedChange={(v) =>
                update({
                  indicators: v
                    ? info!.indicators | RGB_IND_LAYER
                    : info!.indicators & ~RGB_IND_LAYER,
                })}
              id="ind-layer"
              title="Fn layer"
              description="While Fn is held, the keys that do something on it light up"
            />
            <Switch
              checked={(info.indicators & RGB_IND_PROFILE) !== 0}
              onCheckedChange={(v) =>
                update({
                  indicators: v
                    ? info!.indicators | RGB_IND_PROFILE
                    : info!.indicators & ~RGB_IND_PROFILE,
                })}
              id="ind-profile"
              title="Profile"
              description="After switching profile, its number key flashes"
            />
            <ColorPicker
              hue={info.indicatorHue}
              sat={info.indicatorSat}
              title="Indicator colour"
              onchange={(h, s) => update({ indicatorHue: h, indicatorSat: s })}
            />
          </div>
          <div class={cn(card, "content-start")}>
            <span class={sectionTitle}>Idle dimming</span>
            <span class="text-sm text-muted-foreground">
              Fade the lighting out when you have not typed for a while. Any key
              brings it straight back.
            </span>
            <Select.Root
              bind:value={
                () => String(info?.idleMinutes ?? 0),
                (v) => update({ idleMinutes: Number(v) })
              }
              type="single"
            >
              <Select.Trigger class="w-56" size="sm">
                {info.idleMinutes === 0
                  ? "Never"
                  : `After ${info.idleMinutes} minute${info.idleMinutes === 1 ? "" : "s"}`}
              </Select.Trigger>
              <Select.Content>
                {#each IDLE_OPTIONS as m (m)}
                  <Select.Item value={String(m)}>
                    {m === 0
                      ? "Never"
                      : `After ${m} minute${m === 1 ? "" : "s"}`}
                  </Select.Item>
                {/each}
              </Select.Content>
            </Select.Root>
          </div>
        </section>
      {/if}

      <span class="text-xs text-muted-foreground">
        Changes apply instantly and are saved on the keyboard two seconds after
        the last change.
      </span>
    {/if}
  </FixedScrollArea>
</div>
