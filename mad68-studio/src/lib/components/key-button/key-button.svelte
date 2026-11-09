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
  import { cn, type WithElementRef } from "$lib/utils"
  import type { Snippet } from "svelte"
  import type { HTMLButtonAttributes } from "svelte/elements"
  import { tv, type VariantProps } from "tailwind-variants"
  import { toggleVariants } from "../ui/toggle"

  const keyButtonVariants = tv({
    base: cn(
      toggleVariants({ variant: "outline" }),
      "size-full flex-col gap-0 p-0 text-[length:inherit] leading-[1em] whitespace-pre-wrap [&_svg:not([class*='size-'])]:size-[1em]",
      // A keycap: rounded top, darker skirt at the bottom edge
      "rounded-[0.32em] border-keycap-edge bg-keycap font-semibold text-keycap-legend shadow-[inset_0_-0.16em_0_var(--keycap-edge),inset_0_1px_0_rgb(255_255_255/0.07)] transition-[background-color,box-shadow,transform] duration-100 hover:bg-keycap-hover hover:text-keycap-legend active:translate-y-px",
      // Selected: lit in the accent colour
      "data-[state=on]:border-primary data-[state=on]:bg-[color-mix(in_oklab,var(--primary)_9%,var(--keycap-hover))] data-[state=on]:text-foreground data-[state=on]:shadow-[inset_0_0_0_1px_var(--primary),inset_0_-0.16em_0_color-mix(in_oklab,var(--primary)_55%,var(--keycap-edge)),0_0_0.7em_color-mix(in_oklab,var(--primary)_30%,transparent)]",
    ),
    variants: {
      size: {
        default: "[&_span]:text-[length:0.875em]",
        sm: "[&_span]:text-[length:0.75em]",
      },
    },
    defaultVariants: { size: "default" },
  })

  let {
    children,
    class: className,
    ref = $bindable(null),
    size = "default",
    child,
    ...props
  }: WithElementRef<HTMLButtonAttributes> &
    VariantProps<typeof keyButtonVariants> & {
      child?: Snippet<[{ props: Record<string, unknown> }]>
    } = $props()

  const mergedProps = $derived({
    children,
    class: cn(keyButtonVariants({ size }), className),
    ...props,
  })
</script>

{#if child}
  {@render child({ props: mergedProps })}
{:else}
  <button bind:this={ref} {...mergedProps}>
    {@render children?.()}
  </button>
{/if}
