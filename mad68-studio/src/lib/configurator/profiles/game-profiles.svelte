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
    Gamepad2Icon,
    PlusIcon,
    RefreshCwIcon,
    Trash2Icon,
  } from "@lucide/svelte"
  import Switch from "$lib/components/switch.svelte"
  import { Button } from "$lib/components/ui/button"
  import { Input } from "$lib/components/ui/input"
  import * as Popover from "$lib/components/ui/popover"
  import * as Select from "$lib/components/ui/select"
  import { desktop, type WindowedApp } from "$lib/desktop"
  import { gameProfiles } from "$lib/game-profiles.svelte"
  import { keyboardContext } from "$lib/keyboard"
  import { toast } from "svelte-sonner"
  import { globalStateContext } from "../context.svelte"

  const keyboard = keyboardContext.get()
  const { numProfiles } = keyboard.metadata
  const globalState = globalStateContext.get()
  const api = desktop()

  const settings = $derived(gameProfiles.settings)
  const runningRule = $derived(
    settings.rules.find((r) => r.exe === gameProfiles.watch.game),
  )
  const defaultProfile = numProfiles > 1 ? 1 : 0

  // Firmware v6 is needed to switch profiles from the app
  let firmwareOk = $state<boolean | null>(null)
  $effect(() => {
    keyboard.getGamepadState().then((s) => (firmwareOk = s !== null))
  })

  let pickerOpen = $state(false)
  let apps = $state<WindowedApp[] | null>(null)
  let loadingApps = $state(false)
  let typed = $state("")

  async function loadApps() {
    if (!api) return
    loadingApps = true
    try {
      const listed = settings.rules.map((r) => r.exe)
      apps = (await api.windowedApps())
        .filter((a) => !listed.includes(a.exe) && a.exe !== "mad68 studio.exe")
        // one entry per program
        .filter((a, i, all) => all.findIndex((b) => b.exe === a.exe) === i)
        .sort((a, b) => a.title.localeCompare(b.title))
    } finally {
      loadingApps = false
    }
  }

  function addGame(exe: string, name: string) {
    if (gameProfiles.add(exe, name, defaultProfile)) {
      toast.success(
        `${name || exe} added: it will use Profile ${defaultProfile}.`,
      )
      pickerOpen = false
      typed = ""
    } else {
      toast.error("Enter the game's program name, for example game.exe.")
    }
  }

  const shortTitle = (title: string) =>
    title.length > 48 ? `${title.slice(0, 47)}…` : title
</script>

<section
  class="grid gap-4 rounded-lg border bg-card p-4 text-card-foreground shadow-sm"
>
  <div class="flex flex-wrap items-start gap-4">
    <div class="grid flex-1 gap-1">
      <span class="flex items-center gap-2 font-semibold">
        <Gamepad2Icon class="size-4" /> Profiles for Games
      </span>
      <span class="text-sm text-muted-foreground">
        While a game below is running, the keyboard switches to its profile, for
        example one with controller bindings. When the game closes, the profile
        you had before comes back.
      </span>
    </div>
    {#if api}
      <Switch
        bind:checked={() => settings.enabled, (v) => gameProfiles.setEnabled(v)}
        id="game-profiles-enabled"
        title="Switch automatically"
      />
    {/if}
  </div>

  {#if !api}
    <p class="text-sm text-muted-foreground">
      Available in the MAD68 Studio desktop app, which can see which games are
      running.
    </p>
  {:else}
    {#if firmwareOk === false || gameProfiles.supported === false}
      <div
        class="flex flex-wrap items-center gap-3 rounded-lg border border-primary/40 bg-primary/5 p-3 text-sm"
      >
        <span class="flex-1">
          Switching profiles from the app needs firmware v6.
        </span>
        <Button onclick={() => (globalState.tab = "firmware")} size="sm">
          <CpuIcon /> Update firmware
        </Button>
      </div>
    {/if}

    {#if runningRule && settings.enabled}
      <div class="rounded-md bg-primary/10 px-3 py-2 text-sm" role="status">
        <span class="font-bold">{runningRule.name}</span> is running: using
        Profile
        {runningRule.profile}.
      </div>
    {/if}

    {#if settings.rules.length === 0}
      <p class="text-sm text-muted-foreground">
        No games yet. Start a game, then add it here.
      </p>
    {:else}
      <ul class="grid gap-2">
        {#each settings.rules as rule (rule.exe)}
          <li class="flex items-center gap-3 rounded-md border px-3 py-2">
            <div class="grid min-w-0 flex-1">
              <span class="truncate text-sm font-medium">{rule.name}</span>
              <span class="truncate text-xs text-muted-foreground"
                >{rule.exe}</span
              >
            </div>
            <Select.Root
              bind:value={
                () => String(rule.profile),
                (v) => gameProfiles.setProfile(rule.exe, Number(v))
              }
              type="single"
            >
              <Select.Trigger
                aria-label="Profile for {rule.name}"
                class="w-32"
                size="sm"
              >
                Profile {rule.profile}
              </Select.Trigger>
              <Select.Content>
                {#each { length: numProfiles }, i (i)}
                  <Select.Item value={String(i)}>Profile {i}</Select.Item>
                {/each}
              </Select.Content>
            </Select.Root>
            <Button
              aria-label="Remove {rule.name}"
              onclick={() => gameProfiles.remove(rule.exe)}
              size="icon"
              variant="ghost"
            >
              <Trash2Icon />
            </Button>
          </li>
        {/each}
      </ul>
    {/if}

    <div class="flex flex-wrap items-center gap-3">
      <Popover.Root
        bind:open={
          () => pickerOpen,
          (v) => {
            pickerOpen = v
            if (v) loadApps()
          }
        }
      >
        <Popover.Trigger>
          {#snippet child({ props })}
            <Button size="sm" variant="outline" {...props}>
              <PlusIcon /> Add a game
            </Button>
          {/snippet}
        </Popover.Trigger>
        <Popover.Content align="start" class="w-96 p-0">
          <div class="flex items-center justify-between border-b px-3 py-2">
            <span class="text-sm font-bold">Open programs</span>
            <Button
              aria-label="Refresh the list"
              disabled={loadingApps}
              onclick={loadApps}
              size="icon"
              variant="ghost"
            >
              <RefreshCwIcon class={loadingApps ? "animate-spin" : ""} />
            </Button>
          </div>
          <div class="max-h-64 overflow-y-auto p-1">
            {#if loadingApps && !apps}
              <p class="px-2 py-3 text-sm text-muted-foreground">Looking...</p>
            {:else if apps && apps.length === 0}
              <p class="px-2 py-3 text-sm text-muted-foreground">
                No other programs with a window are open. Start the game first,
                or type its program name below.
              </p>
            {:else if apps}
              {#each apps as app (app.exe)}
                <button
                  class="grid w-full rounded-sm px-2 py-1.5 text-left hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
                  onclick={() => addGame(app.exe, app.title)}
                  type="button"
                >
                  <span class="truncate text-sm">{shortTitle(app.title)}</span>
                  <span class="truncate text-xs text-muted-foreground"
                    >{app.exe}</span
                  >
                </button>
              {/each}
            {/if}
          </div>
          <form
            class="flex gap-2 border-t p-2"
            onsubmit={(e) => {
              e.preventDefault()
              addGame(typed, typed)
            }}
          >
            <Input
              aria-label="Program name"
              bind:value={typed}
              class="h-8"
              placeholder="or type it: game.exe"
            />
            <Button disabled={!typed.trim()} size="sm" type="submit">Add</Button
            >
          </form>
        </Popover.Content>
      </Popover.Root>
      <span class="text-xs text-muted-foreground">
        Works while MAD68 Studio is running. With games listed, closing the
        window keeps it in the tray.
      </span>
    </div>
  {/if}
</section>
