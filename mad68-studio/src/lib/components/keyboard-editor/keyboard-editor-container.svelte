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
  import { cn } from "$lib/utils"
  import type { HTMLAttributes } from "svelte/elements"

  const {
    children,
    class: className,
    card = false,
    ...props
  }: HTMLAttributes<HTMLDivElement> & {
    // The settings area under the keyboard, shown as a panel
    card?: boolean
  } = $props()
</script>

<div
  class={cn(
    "mx-auto w-full max-w-7xl flex-1",
    card && "mb-4 w-[calc(100%-2rem)] rounded-2xl border bg-card shadow-sm",
    className,
  )}
  {...props}
>
  {@render children?.()}
</div>
