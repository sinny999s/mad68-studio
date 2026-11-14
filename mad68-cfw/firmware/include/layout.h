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

/**
 * @brief Pause typing for a while, or end the pause (v7.6)
 *
 * While typing is paused the layout reads every key as released: keys held
 * when it starts are released, and nothing reaches the PC (keyboard, mouse,
 * media and power keys, layer and profile keys, the controller). Key readings
 * and calibration carry on. The pause ends by itself once `ms` milliseconds
 * pass without another call, so a host that stops asking can never leave the
 * keyboard silent. A key held when the pause ends stays silent until it is
 * let go. Not saved: a power-up always types.
 *
 * @param ms How long to stay paused from now, 0 to end the pause now
 *
 * @return None
 */
void layout_pause_input(uint32_t ms);

/**
 * @brief Whether typing is paused
 *
 * @param ms_left If not NULL, set to the milliseconds left (0 when not paused)
 *
 * @return true while typing is paused
 */
bool layout_input_paused(uint32_t *ms_left);

/**
 * @brief Number of keys kept silent because they were held when a pause ended
 *
 * @return Key count
 */
uint8_t layout_keys_held_over(void);
