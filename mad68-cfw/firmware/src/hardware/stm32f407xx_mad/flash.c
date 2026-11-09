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

// Flash driver for the MAD68 HE port.
//
// The vendor bootloader lives in sector 0 and the stock firmware's settings in
// sectors 1-2; this firmware itself is programmed into sector 5 by the vendor
// bootloader. None of that may ever be erased or written from here. Every
// erase/write is therefore checked against the wear-leveling window
// [WL_BASE_ADDRESS, FLASH_SIZE) and refused otherwise, independent of what the
// caller asks for.

#include "hardware/hardware.h"

#include "stm32f4xx_hal.h"

static const uint32_t sector_sizes[] = FLASH_SECTOR_SIZES;

_Static_assert(M_ARRAY_SIZE(sector_sizes) == FLASH_NUM_SECTORS,
               "Invalid number of flash sectors");
_Static_assert(WL_BASE_ADDRESS >= 0x60000,
               "Wear leveling must not overlap the app or vendor regions");

static uint32_t sector_offset(uint32_t sector) {
  uint32_t offset = 0;
  for (uint32_t i = 0; i < sector; i++)
    offset += sector_sizes[i];
  return offset;
}

static bool in_wl_window(uint32_t addr, uint32_t len_bytes) {
  return addr >= WL_BASE_ADDRESS && addr + len_bytes <= FLASH_SIZE &&
         addr + len_bytes >= addr;
}

void flash_init(void) {}

bool flash_erase(uint32_t sector) {
  if (sector >= FLASH_NUM_SECTORS)
    return false;
  // Only sectors entirely inside the wear-leveling window may be erased.
  if (!in_wl_window(sector_offset(sector), sector_sizes[sector]))
    return false;

  FLASH_EraseInitTypeDef erase = {0};
  uint32_t error = 0;
  bool success = true;

  HAL_FLASH_Unlock();
  erase.TypeErase = FLASH_TYPEERASE_SECTORS;
  erase.Banks = FLASH_BANK_1;
  erase.Sector = sector;
  erase.NbSectors = 1;
  erase.VoltageRange = FLASH_VOLTAGE_RANGE_3;
  success = (HAL_FLASHEx_Erase(&erase, &error) == HAL_OK);
  HAL_FLASH_Lock();

  return success;
}

bool flash_read(uint32_t addr, void *buf, uint32_t len) {
  if (addr + len * 4 > FLASH_SIZE)
    return false;

  memcpy(buf, (void *)(FLASH_BASE + addr), len * 4);

  return true;
}

bool flash_write(uint32_t addr, const void *buf, uint32_t len) {
  if (!in_wl_window(addr, len * 4))
    return false;

  const uint32_t *buf32 = buf;

  HAL_FLASH_Unlock();
  for (uint32_t i = 0; i < len; i++) {
    if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, FLASH_BASE + addr + i * 4,
                          buf32[i]) != HAL_OK) {
      HAL_FLASH_Lock();
      return false;
    }
  }
  HAL_FLASH_Lock();

  return true;
}
