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

#pragma once

#include "common.h"

//--------------------------------------------------------------------+
// Layout API
//--------------------------------------------------------------------+

/**
 * @brief Initialize the layout module
 *
 * @return None
 */
void layout_init(void);

/**
 * @brief Load advanced keys
 *
 * This function loads the advanced keys from the current profile. It should be
 * called whenever the profile changes or the advanced keys are updated.
 *
 * @return None
 */
void layout_load_advanced_keys(void);

/**
 * @brief Layout task
 *
 * @return None
 */
void layout_task(void);

/**
 * @brief Highest active layer (for board lighting indicators)
 *
 * @return Current layer
 */
uint8_t layout_current_layer(void);

/**
 * @brief Manually register a key press
 *
 * @param key Key code
 * @param keycode Key code
 *
 * @return None
 */
void layout_register(uint8_t key, uint8_t keycode);

/**
 * @brief Manually register a key release
 *
 * @param key Key code
 * @param keycode Key code
 *
 * @return None
 */
void layout_unregister(uint8_t key, uint8_t keycode);

/**
 * @brief Switch to a profile, as the profile keys do
 *
 * Does nothing (and writes no flash) if the profile is already the current
 * one.
 *
 * @param profile Profile index
 *
 * @return true if successful, false if the profile does not exist
 */
bool layout_switch_profile(uint8_t profile);
