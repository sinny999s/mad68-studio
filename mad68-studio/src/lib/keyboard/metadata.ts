/*
 * This program is free software: you can redistribute it and/or modify it under
 * the terms of the GNU General Public License as published by the Free Software
 * Foundation, either version 3 of the License, or (at your option) any later
 * version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
 * FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
 * details.
 *
 * You should have received a copy of the GNU General Public License along with
 * this program. If not, see <https://www.gnu.org/licenses/>.
 */

import { uint8Schema, uint16Schema } from "$lib/integer"
import {
  HMK_MAX_NUM_ADVANCED_KEYS,
  HMK_MAX_NUM_KEYS,
  HMK_MAX_NUM_LAYERS,
  HMK_MAX_NUM_MACRO_NODES,
  HMK_MAX_NUM_PROFILES,
} from "$lib/libhmk"
import z from "zod"
import { Keycode, MO, PF } from "../libhmk/keycodes"
import mad68Metadata from "./mad68-metadata.json"

const uint16HexSchema = z.union([
  uint16Schema,
  z
    .string()
    .regex(/0x[0-9a-fA-F]{4}/)
    .transform((val) => parseInt(val, 16)),
])

const keycodeSchema = z.union([
  uint8Schema,
  z.string().transform((val, ctx) => {
    const moKeycode = val.match(/^MO\((\d+\))$/)
    if (moKeycode) return MO(parseInt(moKeycode[1]))

    const pfKeycode = val.match(/^PF\((\d+\))$/)
    if (pfKeycode) return PF(parseInt(pfKeycode[1]))

    if (!Object.keys(Keycode).includes(val)) {
      ctx.addIssue({
        code: "custom",
        message: `Unknown keycode: ${val}`,
        input: val,
      })
      return z.NEVER
    }

    return Keycode[val as keyof typeof Keycode]
  }),
])

const keyboardLayoutSchema = z
  .object({
    labels: z
      .array(z.union([z.string(), z.array(z.string()).min(3)]))
      .default([]),
    keymap: z.array(
      z.array(
        z.object({
          key: uint8Schema,
          w: z.number().min(1).default(1),
          h: z.number().min(1).default(1),
          x: z.number().default(0),
          y: z.number().default(0),
          option: z.tuple([z.int().min(0), z.int().min(0)]).optional(),
        }),
      ),
    ),
  })
  .superRefine((val, ctx) => {
    const optionMaxValues = val.labels.map((l) =>
      typeof l === "string" ? 2 : l.length - 1,
    )
    for (const { option } of val.keymap.flat()) {
      if (option === undefined) continue
      if (
        option[0] >= val.labels.length ||
        option[1] >= optionMaxValues[option[0]]
      ) {
        ctx.addIssue({
          code: "custom",
          message: "Option key or value is out of range",
          input: option,
        })
      }
    }
  })

export type KeyboardLayout = z.infer<typeof keyboardLayoutSchema>

export const keyboardMetadataSchema = z
  .object({
    name: z.string(),
    vendorId: uint16HexSchema,
    productId: uint16HexSchema,
    usbHighSpeed: z.boolean().default(false),

    adcResolution: z.int().min(1).max(16),
    numProfiles: z.int().min(1).max(HMK_MAX_NUM_PROFILES),
    numLayers: z.int().min(1).max(HMK_MAX_NUM_LAYERS),
    numKeys: z.int().min(1).max(HMK_MAX_NUM_KEYS),
    numAdvancedKeys: z.int().min(1).max(HMK_MAX_NUM_ADVANCED_KEYS),
    numDynamicKeystrokeMaxBindings: z
      .int()
      .min(1)
      .max(HMK_MAX_NUM_ADVANCED_KEYS)
      .default(4),
    numMacroNodes: z.int().min(1).max(HMK_MAX_NUM_MACRO_NODES).default(128),

    layout: keyboardLayoutSchema,
    defaultKeymap: z.array(z.array(keycodeSchema)).optional(),
    defaultKeymaps: z.array(z.array(z.array(keycodeSchema))).optional(),
  })
  .transform((val, ctx) => {
    const getDefaultKeymaps = () => {
      const defaultKeymaps = val.defaultKeymaps
      if (defaultKeymaps !== undefined) {
        return defaultKeymaps
      } else {
        const defaultKeymap = val.defaultKeymap
        return defaultKeymap === undefined
          ? undefined
          : [...Array(val.numProfiles)].map(() =>
              defaultKeymap.map((layer) => [...layer]),
            )
      }
    }

    const defaultKeymaps = getDefaultKeymaps()
    if (defaultKeymaps === undefined) {
      ctx.addIssue({
        code: "custom",
        message: "Expected either defaultKeymap or defaultKeymaps",
      })
      return z.NEVER
    }

    if (defaultKeymaps.length !== val.numProfiles) {
      ctx.addIssue({
        code: "custom",
        message: `Expected defaultKeymaps to have ${val.numProfiles} profiles`,
      })
    }

    if (defaultKeymaps.some((profile) => profile.length !== val.numLayers)) {
      ctx.addIssue({
        code: "custom",
        message: `Expected defaultKeymaps profiles to have ${val.numLayers} layers`,
      })
    }

    if (
      defaultKeymaps.some((profile) =>
        profile.some((layer) => layer.length !== val.numKeys),
      )
    ) {
      ctx.addIssue({
        code: "custom",
        message: `Expected defaultKeymaps layers to have ${val.numKeys} keys`,
      })
    }

    return { ...val, defaultKeymaps }
  })

export type KeyboardMetadata = z.infer<typeof keyboardMetadataSchema>

// The demo plays a MAD68 HE: the same layout, default keymaps and sizes that
// the custom firmware reports (from mad68-cfw/firmware/keyboards/mad68he).
export const demoMetadata = keyboardMetadataSchema.parse({
  ...mad68Metadata,
  name: "MAD68 HE",
})
