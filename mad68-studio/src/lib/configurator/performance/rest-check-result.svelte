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
What a rest check found, set against the actuation points of the profile the
page is showing. The verdicts follow the actuation map live, so they change
when a key's actuation point does.
-->
<script lang="ts">
  import { KeyboardEditorKeyboard } from "$lib/components/keyboard-editor"
  import { Badge } from "$lib/components/ui/badge"
  import { Button } from "$lib/components/ui/button"
  import { displayDistance } from "$lib/distance"
  import { cn } from "$lib/utils"
  import { displayLayoutContext, globalStateContext } from "../context.svelte"
  import { actuationQueryContext } from "../queries/actuation-query.svelte"
  import {
    buildRestReport,
    CLOSE_ROOM_MM,
    formatDuration,
    formatReading,
    formatRoom,
    HAND_PRESS_MM,
    LOWEST_MARGIN_MM,
    LOWEST_MINIMUM_MM,
    summarizeRest,
    type RestCheckEnd,
    type RestKeyReport,
    type RestMeasurement,
  } from "./rest-check"

  const {
    measurement,
    ended,
    keyName,
  }: {
    measurement: RestMeasurement
    ended: RestCheckEnd
    keyName: (key: number) => string
  } = $props()

  const { profile } = $derived(globalStateContext.get())
  const displayLayout = displayLayoutContext.get()
  const actuationQuery = actuationQueryContext.get()
  const { current: actuationMap } = $derived(actuationQuery.actuationMap)

  const report = $derived(
    actuationMap &&
      buildRestReport(
        measurement,
        actuationMap,
        displayLayout.displayKeys.map(({ key }) => key),
      ),
  )

  // The same call the sliders below the keyboard make
  const setActuationPoint = ({ key, lowest }: RestKeyReport) =>
    actuationMap &&
    actuationQuery.set({
      offset: key,
      data: [{ ...actuationMap[key], actuationPoint: lowest }],
    })

  const looks = {
    pressed: {
      title: "Would have pressed",
      keycap: "border-destructive bg-destructive",
    },
    close: { title: "Close", keycap: "border-primary bg-primary" },
    fine: { title: "Moved, fine", keycap: "border-chart-4 bg-chart-4/45" },
    "by-hand": {
      title: "Pressed during the check",
      keycap: "border-dashed border-muted-foreground bg-transparent",
    },
  }
  const still = "border-keycap-edge bg-keycap"

  const look = (keyReport: RestKeyReport | undefined) =>
    keyReport && (keyReport.deepest > 0 || keyReport.verdict !== "fine")
      ? looks[keyReport.verdict]
      : undefined

  const rate = $derived(
    measurement.elapsedMs > 0
      ? Math.round((measurement.readings * 1000) / measurement.elapsedMs)
      : 0,
  )
  // The rules, as they are worded below
  const handPress = HAND_PRESS_MM.toFixed(2)
  const closeRoom = CLOSE_ROOM_MM.toFixed(2)
  const lowestMargin = LOWEST_MARGIN_MM.toFixed(2)
  const lowestMinimum = LOWEST_MINIMUM_MM.toFixed(2)

  const count = (n: number, one: string, many: string) =>
    `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`
</script>

{#if report}
  {@const shown = [...report.listed, ...report.byHand]}
  <p class="text-base font-medium text-wrap" aria-live="polite">
    {summarizeRest(report, keyName)}
  </p>

  {#if ended === "failed"}
    <p class="text-sm text-wrap text-destructive" role="alert">
      The keyboard stopped answering after {formatDuration(
        measurement.elapsedMs,
      )}, so the check ended early. This is what it saw until then.
    </p>
  {/if}

  {#if shown.length > 0}
    <!-- The same keys as the table and the lines below it, by position -->
    <div class="grid gap-2" aria-hidden="true">
      <div class="flex h-32 flex-col">
        <KeyboardEditorKeyboard class="p-2">
          {#snippet keyGenerator(key)}
            {@const keyReport = report.keys.get(key)}
            {@const keyLook = look(keyReport)}
            <div
              class={cn(
                "size-full rounded-[0.32em] border",
                keyLook?.keycap ?? still,
              )}
              title={keyLook && keyReport
                ? keyReport.verdict === "by-hand"
                  ? `${keyName(key)}: pressed during the check`
                  : `${keyName(key)}: read up to ${formatReading(keyReport.deepest)} mm`
                : undefined}
            ></div>
          {/snippet}
        </KeyboardEditorKeyboard>
      </div>
      <ul
        class="flex flex-wrap justify-center gap-x-4 gap-y-1 text-xs text-muted-foreground"
      >
        {#each Object.entries(looks) as [verdict, { title, keycap }] (verdict)}
          {#if shown.some((keyReport) => keyReport.verdict === verdict)}
            <li class="flex items-center gap-1.5">
              <span class={cn("size-3 rounded-[3px] border", keycap)}></span>
              {title}
            </li>
          {/if}
        {/each}
        <li class="flex items-center gap-1.5">
          <span class={cn("size-3 rounded-[3px] border", still)}></span>
          Never moved
        </li>
      </ul>
    </div>
  {/if}

  {#if report.listed.length > 0}
    <div class="max-h-56 overflow-auto rounded-lg border">
      <table class="w-full text-sm tabular-nums">
        <thead
          class="sticky top-0 bg-popover text-xs text-muted-foreground shadow-[0_1px_0_var(--border)]"
        >
          <tr
            class="[&>th]:px-3 [&>th]:py-2 [&>th]:align-bottom [&>th]:font-medium"
          >
            <th class="text-left">Key</th>
            <th class="text-right">Deepest at rest</th>
            <th class="text-right">Times it moved</th>
            <th class="text-right">Actuation point</th>
            <th class="text-right">Room</th>
            <th class="text-left">Rapid Trigger</th>
            <th class="text-left">Lowest to try</th>
            <th class="text-left">Verdict</th>
          </tr>
        </thead>
        <tbody class="divide-y">
          {#each report.listed as keyReport (keyReport.key)}
            {@const { key, deepest, departures, verdict } = keyReport}
            {@const lowest = displayDistance(keyReport.lowest)}
            <tr class="[&>td]:px-3 [&>td]:py-2 [&>td]:whitespace-nowrap">
              <td class="font-medium">{keyName(key)}</td>
              <td class="text-right">{formatReading(deepest)} mm</td>
              <td class="text-right">{departures.toLocaleString("en-US")}</td>
              <td class="text-right">
                {displayDistance(keyReport.actuationPoint)} mm
              </td>
              <td class="text-right">
                {#if verdict === "pressed"}
                  none
                {:else}
                  {formatRoom(keyReport.room)} mm
                {/if}
              </td>
              <td>{keyReport.rapidTrigger ? "On" : "Off"}</td>
              <td>
                <span class="flex items-center gap-2">
                  {lowest} mm
                  {#if keyReport.lowest !== keyReport.actuationPoint}
                    <Button
                      aria-label="Set the actuation point of {keyName(
                        key,
                      )} to {lowest} mm"
                      onclick={() => setActuationPoint(keyReport)}
                      size="xs"
                      variant="outline"
                    >
                      {keyReport.lowest > keyReport.actuationPoint
                        ? "Raise"
                        : "Lower"}
                    </Button>
                  {/if}
                </span>
              </td>
              <td>
                {#if verdict === "pressed"}
                  <Badge variant="destructive">Would have pressed</Badge>
                {:else if verdict === "close"}
                  <Badge class="border-primary/60 bg-primary/20 text-foreground"
                    >Close</Badge
                  >
                {:else}
                  <Badge variant="outline">Fine</Badge>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  <div class="grid gap-1 text-sm text-wrap">
    {#if report.still > 0}
      <p>
        {report.listed.length + report.byHand.length === 0
          ? `All ${report.still} keys`
          : `The other ${count(report.still, "key", "keys")}`}
        read 0.00 mm every time.
      </p>
    {/if}
    {#if report.byHand.length > 0}
      <p>
        Pressed during the check: {report.byHand
          .map(({ key }) => keyName(key))
          .join(", ")}. A key that goes past {handPress} mm was pressed by hand, so
        it gets no verdict.
      </p>
    {/if}
  </div>

  <p class="text-sm text-wrap text-muted-foreground">
    Every key was read {count(measurement.readings, "time", "times")} in
    {formatDuration(measurement.elapsedMs)}{rate > 0
      ? `, about ${rate} times a second`
      : ""}. The result only covers that time, and the keyboard reads its keys
    far more often than the app can ask, so a movement that lasts a moment can
    fall between two readings. A longer check finds rarer movements. The check
    itself changed nothing on the keyboard.
  </p>
  {#if report.listed.length > 0}
    <p class="text-sm text-wrap text-muted-foreground">
      Room is the actuation point in Profile {profile} minus the deepest resting reading:
      under {closeRoom} mm is close, and none left means the key would have pressed
      by itself. Lowest to try is the deepest reading plus {lowestMargin} mm, on the
      next slider step up and never under {lowestMinimum} mm. A key set that low shows
      as close.
    </p>
  {/if}
{/if}
