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

// Board support for the MadLions MAD68 HE (DuckBread controller).
//
// Everything hardware-specific here was taken from the stock v1.06 firmware
// running in an emulator (see mad68-cfw/tools): the GPIO register values, the
// clock tree and the exact sequence the stock firmware uses to hand control to
// the vendor IAP bootloader. The fail-safe logic makes sure that a build which
// does not come up properly drops back into that bootloader, where the stock
// firmware can be flashed again from the official web tool.

#include "hardware/hardware.h"
#include "rgb.h"
#include "eeconfig.h"
#include "layout.h"
#include "xinput.h"

#include "stm32f4xx_hal.h"
#include "tusb.h"
#include "device/dcd.h"

//--------------------------------------------------------------------+
// Stock GPIO state
//--------------------------------------------------------------------+

typedef struct {
  GPIO_TypeDef *port;
  uint32_t moder, otyper, ospeedr, pupdr, odr, afrl, afrh;
} gpio_state_t;

// Final GPIO configuration left by the stock firmware's init code, captured
// from emulation. ODR differs in one place only: every sensor column
// (PA8-12, PA15, PC10-12, PD2, PB3-7, all active low) starts deselected.
static const gpio_state_t stock_gpio[] = {
    {GPIOA, 0x6955EAFF, 0x00000000, 0x3C002B00, 0x00000000, 0x0000FFFF,
     0x05560000, 0x00000000},
    {GPIOB, 0xA008554C, 0x00000200, 0xFFFF0033, 0x05510011, 0x0000FFFF,
     0x00000000, 0xCC000040},
    {GPIOC, 0x01508001, 0x00000000, 0xFC0FFFFF, 0x54051554, 0x0000FFFF,
     0x60000000, 0x00000000},
    {GPIOD, 0x55000110, 0x00000000, 0xFFFFFFCF, 0x00555045, 0x00000FFF,
     0x00000000, 0x00000000},
    {GPIOE, 0x00000040, 0x00000000, 0xFFFFFFFF, 0x00000000, 0x0000FFFF,
     0x00000000, 0x00000000},
};

static void board_gpio_init(void) {
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOE_CLK_ENABLE();

  for (uint32_t i = 0; i < M_ARRAY_SIZE(stock_gpio); i++) {
    const gpio_state_t *s = &stock_gpio[i];
    // Output levels first so pins come up at the right level, mode last.
    s->port->ODR = s->odr;
    s->port->OTYPER = s->otyper;
    s->port->OSPEEDR = s->ospeedr;
    s->port->PUPDR = s->pupdr;
    s->port->AFR[0] = s->afrl;
    s->port->AFR[1] = s->afrh;
    s->port->MODER = s->moder;
  }
}

//--------------------------------------------------------------------+
// Clock
//--------------------------------------------------------------------+

// Stock: HSE 16 MHz, PLLM 16, PLLN 336, PLLP 2, PLLQ 7 -> 168 MHz SYSCLK,
// 48 MHz USB, AHB /1, APB1 /4, APB2 /2, 5 wait states.
static void board_clock_init(void) {
  RCC_OscInitTypeDef rcc_osc_init = {0};
  RCC_ClkInitTypeDef rcc_clk_init = {0};

  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  rcc_osc_init.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  rcc_osc_init.HSEState = RCC_HSE_ON;
  rcc_osc_init.PLL.PLLState = RCC_PLL_ON;
  rcc_osc_init.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  rcc_osc_init.PLL.PLLM = BOARD_HSE_VALUE / 1000000;
  rcc_osc_init.PLL.PLLN = 336;
  rcc_osc_init.PLL.PLLP = RCC_PLLP_DIV2;
  rcc_osc_init.PLL.PLLQ = 7;
  if (HAL_RCC_OscConfig(&rcc_osc_init) != HAL_OK)
    board_error_handler();

  rcc_clk_init.ClockType = RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_HCLK |
                           RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
  rcc_clk_init.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  rcc_clk_init.AHBCLKDivider = RCC_SYSCLK_DIV1;
  rcc_clk_init.APB1CLKDivider = RCC_HCLK_DIV4;
  rcc_clk_init.APB2CLKDivider = RCC_HCLK_DIV2;
  if (HAL_RCC_ClockConfig(&rcc_clk_init, FLASH_LATENCY_5) != HAL_OK)
    board_error_handler();

  // HAL re-arms SysTick at its default priority; the fail-safe watchdog needs
  // it at the top so it still runs if some other interrupt never returns.
  HAL_InitTick(0);
}

//--------------------------------------------------------------------+
// USB (OTG_HS core on PB14/PB15, pins already set by board_gpio_init)
//--------------------------------------------------------------------+

static void board_usb_init(void) {
  // Exactly the stock firmware's sequence (v1.06 @ 0x08030442..0x080304B0),
  // which is what brings this chip's USB up:
  //   1. clock on (also in sleep), pulse-reset the OTG_HS block
  //   2. byte-wide writes to a vendor PHY control block at OTG_HS + 0x200
  //      (not part of the Synopsys core; powers the on-chip HS PHY)
  //   3. GUSBCFG / DCFG / PCGCCTL / GCCFG preset to stock values before the
  //      core reset that TinyUSB performs next
  __HAL_RCC_USB_OTG_HS_CLK_ENABLE();
  __HAL_RCC_USB_OTG_HS_CLK_SLEEP_ENABLE();
  __HAL_RCC_USB_OTG_HS_FORCE_RESET();
  __HAL_RCC_USB_OTG_HS_RELEASE_RESET();

  volatile uint8_t *const phy =
      (volatile uint8_t *)(USB_OTG_HS_PERIPH_BASE + 0x200u);
  phy[0x00] = (uint8_t)(phy[0x00] | 0x01u);
  phy[0x04] = (uint8_t)(phy[0x04] | 0x01u);
  phy[0x10] = (uint8_t)(phy[0x10] | 0x01u);
  phy[0x24] = (uint8_t)((phy[0x24] & ~0x07u) | 0x05u);

  USB_OTG_HS->GUSBCFG = 0x40002400u;
  ((USB_OTG_DeviceTypeDef *)(USB_OTG_HS_PERIPH_BASE + USB_OTG_DEVICE_BASE))
      ->DCFG = 0x02200000u;
  *(__IO uint32_t *)(USB_OTG_HS_PERIPH_BASE + USB_OTG_PCGCCTL_BASE) = 0u;
  USB_OTG_HS->GCCFG = 0x00210000u; // PWRDWN | NOVBUSSENS
}

//--------------------------------------------------------------------+
// Vendor bootloader hand-off
//--------------------------------------------------------------------+

extern uint32_t _board_bootloader_flag[];

// Mirrors the stock firmware's own routine (v1.06 @ 0x08026A78): set the RAM
// magic, pulse-reset every peripheral, put RCC back to its reset state, stop
// SysTick, clear the NVIC, point VTOR at the bootloader and branch to it.
// Must be called from thread mode.
__attribute__((noreturn, used)) void board_vendor_bootloader_jump(void) {
  if (_board_bootloader_flag[0] != BOOTLOADER_MAGIC)
    _board_bootloader_flag[0] = BOOTLOADER_MAGIC;

  __disable_irq();

  RCC->APB1RSTR = 0xFFFFFFFF;
  RCC->APB1RSTR = 0;
  RCC->APB2RSTR = 0xFFFFFFFF;
  RCC->APB2RSTR = 0;
  RCC->AHB1RSTR = 0xFFFFFFFF;
  RCC->AHB1RSTR = 0;
  RCC->AHB2RSTR = 0xFFFFFFFF;
  RCC->AHB2RSTR = 0;

  RCC->CR |= RCC_CR_HSION;
  RCC->CFGR = 0;
  RCC->CR &= ~(0x15000000u | 0x00090000u); // PLL*ON, CSSON, HSEON as stock
  RCC->PLLCFGR = 0x24003010;
  RCC->PLLI2SCFGR = 0x20003000;
  RCC->CR &= ~RCC_CR_HSEBYP;
  RCC->CIR = 0;

  SysTick->CTRL = 0;
  SysTick->LOAD = 0;
  SysTick->VAL = 0;
  for (uint32_t i = 0; i < 8; i++) {
    NVIC->ICER[i] = 0xFFFFFFFF;
    NVIC->ICPR[i] = 0xFFFFFFFF;
  }

  __enable_irq();

  const uint32_t sp = *(volatile const uint32_t *)BOOTLOADER_ADDR;
  const uint32_t entry = *(volatile const uint32_t *)(BOOTLOADER_ADDR + 4);
  SCB->VTOR = BOOTLOADER_ADDR;
  __set_CONTROL(0);
  __ISB();
  __asm volatile("msr msp, %0\n"
                 "bx %1\n" ::"r"(sp),
                 "r"(entry)
                 : "memory");
  while (1)
    ;
}

//--------------------------------------------------------------------+
// Fail-safe
//--------------------------------------------------------------------+

// A build is "confirmed" once it has enumerated over USB and stayed up for
// FAILSAFE_CONFIRM_MS. Confirmation appends the build ID to a journal in flash
// sector 6 (0x08040000-0x0805FFFF), which neither the vendor bootloader, the
// stock firmware nor libhmk's wear leveling use.
//
// While a build is unconfirmed (its first boots), any of these hands control
// back to the vendor bootloader:
//   - USB not mounted within FAILSAFE_USB_TIMEOUT_MS of power-up,
//   - a fault (HardFault / MemManage / BusFault / UsageFault),
//   - board_error_handler().
// Faults and board_error_handler() fall back to the bootloader even on
// confirmed builds, since there is nothing better to do at that point.

// Trial-boot timeline (ms since reset): try high speed with the stock PHY
// setup first, then plain full speed, then give up and hand over to the
// vendor bootloader.
#if !defined(FAILSAFE_HS_ATTEMPT_MS)
#define FAILSAFE_HS_ATTEMPT_MS 4000
#endif
#if !defined(FAILSAFE_USB_TIMEOUT_MS)
#define FAILSAFE_USB_TIMEOUT_MS 9000
#endif
#if !defined(FAILSAFE_CONFIRM_MS)
#define FAILSAFE_CONFIRM_MS 5000
#endif

// Flash sector 6 (0x08040000, 128 KB) is split in two append-only journals:
// the first 64 KB records confirmed builds, the second 64 KB the RGB settings
// (typed records, see rgb.h). When either fills up the sector is erased and
// the latest confirmation and the latest RGB record of each type are written
// back.
#define CONFIRM_JOURNAL_ADDR 0x08040000u
#define CONFIRM_JOURNAL_SIZE (64u * 1024u)
#define CONFIRM_JOURNAL_SECTOR FLASH_SECTOR_6
#define RGB_JOURNAL_ADDR 0x08050000u
#define RGB_JOURNAL_SIZE (64u * 1024u)
// Journal entry for a build confirmed at full speed instead of high speed
#define CONFIRM_FS_XOR 0x5A5A5A5Au

#if !defined(FIRMWARE_BUILD_ID)
#error "FIRMWARE_BUILD_ID must be defined by the build"
#endif

_Static_assert((uint32_t)FIRMWARE_BUILD_ID != 0xFFFFFFFFu &&
                   ((uint32_t)FIRMWARE_BUILD_ID ^ CONFIRM_FS_XOR) != 0xFFFFFFFFu,
               "build ID collides with blank flash");

// Exported so tooling (and a debugger) can read which build this is.
__attribute__((used)) const volatile uint32_t board_firmware_build_id =
    (uint32_t)FIRMWARE_BUILD_ID;

static volatile bool failsafe_armed = false;
static volatile bool failsafe_triggered = false;
static volatile uint32_t mounted_since = 0;
static volatile bool confirm_pending = false;
static volatile bool usb_use_hs = true;
static volatile bool usb_fallback_pending = false;
static volatile bool usb_fallback_done = false;

// TinyUSB hook (scripts/patch_tinyusb.py): which speed/PHY to bring up.
bool board_dwc2_use_highspeed(void) { return usb_use_hs; }

static uint32_t journal_last(uint32_t *next_addr) {
  uint32_t last = 0xFFFFFFFF;
  uint32_t addr = CONFIRM_JOURNAL_ADDR;
  for (; addr < CONFIRM_JOURNAL_ADDR + CONFIRM_JOURNAL_SIZE; addr += 4) {
    const uint32_t v = *(volatile const uint32_t *)addr;
    if (v == 0xFFFFFFFF)
      break;
    last = v;
  }
  if (next_addr)
    *next_addr = addr;
  return last;
}

// Returns true if this build has been confirmed, and sets the USB speed it
// was confirmed at.
static bool build_confirmed(void) {
  const uint32_t last = journal_last(NULL);
  if (last == board_firmware_build_id) {
    usb_use_hs = true;
    return true;
  }
  if (last == (board_firmware_build_id ^ CONFIRM_FS_XOR)) {
    usb_use_hs = false;
    return true;
  }
  return false;
}

static bool program_words(uint32_t addr, const uint32_t *words, uint32_t n) {
  bool ok = true;
  HAL_FLASH_Unlock();
  for (uint32_t i = 0; i < n && ok; i++)
    ok = HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, addr + 4u * i, words[i]) ==
         HAL_OK;
  HAL_FLASH_Lock();
  return ok;
}

//--------------------------------------------------------------------+
// RGB settings journal (second half of sector 6)
//--------------------------------------------------------------------+
//
// Record: [0] 0xB7 [1] type [2..3] payload length (LE) [payload]
//         [CRC-32 of header + payload (LE)], padded with 0xFF to 8 bytes.
// Firmware v4 wrote fixed 8-byte records starting with 0xA5; the latest one
// is still readable so its settings carry over.

#define RGB_REC_MAGIC 0xB7u
#define RGB_LEGACY_MAGIC 0xA5u
#define RGB_LEGACY_SIZE 8u

typedef struct {
  uint32_t addr; // payload address, 0 = none
  uint32_t len;
} rgb_rec_t;

static uint32_t rgb_crc(const uint8_t *p, uint32_t n) {
  uint32_t crc = 0xFFFFFFFFu;
  for (uint32_t i = 0; i < n; i++) {
    crc ^= p[i];
    for (uint32_t b = 0; b < 8; b++)
      crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}

static uint32_t rgb_rec_size(uint32_t len) { return (len + 8u + 7u) & ~7u; }

static bool rgb_crc_ok(uint32_t addr, uint32_t len) {
  uint32_t crc;
  memcpy(&crc, (const void *)(addr + 4u + len), 4);
  return crc == rgb_crc((const uint8_t *)addr, 4u + len);
}

// Latest record of each type (index 0 = legacy 8-byte record); with `verify`
// every record's CRC is checked, otherwise none are. Returns the first free
// address, or the journal end if it is full or damaged.
static uint32_t rgb_walk(rgb_rec_t found[RGB_REC_TYPES], bool verify) {
  const uint32_t end = RGB_JOURNAL_ADDR + RGB_JOURNAL_SIZE;
  for (uint32_t t = 0; t < RGB_REC_TYPES; t++)
    found[t] = (rgb_rec_t){0, 0};
  uint32_t addr = RGB_JOURNAL_ADDR;
  while (addr + 8u <= end) {
    const uint32_t w0 = *(volatile const uint32_t *)addr;
    const uint32_t w1 = *(volatile const uint32_t *)(addr + 4u);
    if (w0 == 0xFFFFFFFFu && w1 == 0xFFFFFFFFu)
      break;
    if ((w0 & 0xFFu) == RGB_LEGACY_MAGIC) {
      found[0] = (rgb_rec_t){addr, RGB_LEGACY_SIZE};
      addr += RGB_LEGACY_SIZE;
      continue;
    }
    if ((w0 & 0xFFu) == RGB_REC_MAGIC) {
      const uint32_t type = (w0 >> 8) & 0xFFu;
      const uint32_t len = w0 >> 16;
      const uint32_t size = rgb_rec_size(len);
      if (len > RGB_REC_MAX_PAYLOAD || addr + size > end)
        return end; // damaged: treat as full, the next store compacts
      if (type > 0 && type < RGB_REC_TYPES && (!verify || rgb_crc_ok(addr, len)))
        found[type] = (rgb_rec_t){addr + 4u, len};
      addr += size;
      continue;
    }
    addr += 8u; // torn or unknown write: skip
  }
  return addr;
}

// Latest valid record of each type. Only the newest records are CRC-checked;
// the whole journal is checked only if one of them is damaged (torn write).
static uint32_t rgb_scan(rgb_rec_t found[RGB_REC_TYPES]) {
  const uint32_t next = rgb_walk(found, false);
  for (uint32_t t = 1; t < RGB_REC_TYPES; t++)
    if (found[t].addr && !rgb_crc_ok(found[t].addr - 4u, found[t].len))
      return rgb_walk(found, true);
  return next;
}

static bool rgb_write(uint32_t addr, uint32_t type, const uint8_t *payload,
                      uint32_t len) {
  uint32_t words[(RGB_REC_MAX_PAYLOAD + 16u) / 4u];
  uint8_t *b = (uint8_t *)words;
  const uint32_t size = rgb_rec_size(len);
  memset(b, 0xFF, size);
  b[0] = RGB_REC_MAGIC;
  b[1] = (uint8_t)type;
  b[2] = (uint8_t)len;
  b[3] = (uint8_t)(len >> 8);
  memcpy(b + 4, payload, len);
  const uint32_t crc = rgb_crc(b, 4u + len);
  memcpy(b + 4 + len, &crc, 4);
  return program_words(addr, words, size / 4u);
}

// Erase sector 6 and write back the given confirmation entry (if any), the
// new RGB record (if any) and the latest RGB record of every other type.
// Only ever touches sector 6.
static bool sector6_reset(uint32_t confirm_entry, bool has_confirm,
                          uint32_t new_type, const uint8_t *new_payload,
                          uint32_t new_len) {
  static uint8_t keep[RGB_REC_TYPES][RGB_REC_MAX_PAYLOAD];
  uint32_t keep_len[RGB_REC_TYPES] = {0};
  rgb_rec_t found[RGB_REC_TYPES];
  rgb_scan(found);
  for (uint32_t t = 0; t < RGB_REC_TYPES; t++) {
    if (t == new_type && new_payload) {
      memcpy(keep[t], new_payload, new_len);
      keep_len[t] = new_len;
    } else if (found[t].addr) {
      memcpy(keep[t], (const void *)found[t].addr, found[t].len);
      keep_len[t] = found[t].len;
    }
  }
  // The legacy record only matters until settings are saved in the new form
  if (keep_len[RGB_REC_CONFIG])
    keep_len[0] = 0;

  FLASH_EraseInitTypeDef erase = {0};
  uint32_t error = 0;
  erase.TypeErase = FLASH_TYPEERASE_SECTORS;
  erase.Banks = FLASH_BANK_1;
  erase.Sector = CONFIRM_JOURNAL_SECTOR;
  erase.NbSectors = 1;
  erase.VoltageRange = FLASH_VOLTAGE_RANGE_3;
  HAL_FLASH_Unlock();
  const bool ok = HAL_FLASHEx_Erase(&erase, &error) == HAL_OK;
  HAL_FLASH_Lock();
  if (!ok)
    return false;
  if (has_confirm)
    program_words(CONFIRM_JOURNAL_ADDR, &confirm_entry, 1);
  uint32_t addr = RGB_JOURNAL_ADDR;
  if (keep_len[0]) {
    uint32_t words[2];
    memcpy(words, keep[0], sizeof(words));
    program_words(addr, words, 2);
    addr += RGB_LEGACY_SIZE;
  }
  for (uint32_t t = 1; t < RGB_REC_TYPES; t++) {
    if (!keep_len[t])
      continue;
    rgb_write(addr, t, keep[t], keep_len[t]);
    addr += rgb_rec_size(keep_len[t]);
  }
  return true;
}

static void confirm_build(void) {
  const uint32_t entry = usb_use_hs
                             ? board_firmware_build_id
                             : (board_firmware_build_id ^ CONFIRM_FS_XOR);
  uint32_t next = 0;
  if (journal_last(&next) == entry)
    return;
  if (next + 4 > CONFIRM_JOURNAL_ADDR + CONFIRM_JOURNAL_SIZE) {
    sector6_reset(entry, true, 0, NULL, 0);
    return;
  }
  program_words(next, &entry, 1);
}

uint32_t board_rgb_load(uint32_t type, uint8_t *payload, uint32_t max_len) {
  rgb_rec_t found[RGB_REC_TYPES];
  rgb_scan(found);
  if (type >= RGB_REC_TYPES || !found[type].addr)
    return 0;
  const uint32_t len = found[type].len;
  memcpy(payload, (const void *)found[type].addr, len < max_len ? len : max_len);
  return len;
}

void board_rgb_store(uint32_t type, const uint8_t *payload, uint32_t len) {
  if (type == 0 || type >= RGB_REC_TYPES || len > RGB_REC_MAX_PAYLOAD)
    return;
  rgb_rec_t found[RGB_REC_TYPES];
  const uint32_t next = rgb_scan(found);
  if (found[type].addr && found[type].len == len &&
      memcmp((const void *)found[type].addr, payload, len) == 0)
    return; // unchanged
  if (next + rgb_rec_size(len) > RGB_JOURNAL_ADDR + RGB_JOURNAL_SIZE) {
    const uint32_t confirm = journal_last(NULL);
    sector6_reset(confirm, confirm != 0xFFFFFFFFu, type, payload, len);
    return;
  }
  rgb_write(next, type, payload, len);
}

//--------------------------------------------------------------------+
// Controller (XInput) and profile commands for MAD68 Studio
//--------------------------------------------------------------------+
//
// GET_GAMEPAD (0x64): no arguments. Reply:
//   [1] protocol version: 1 = v6, 2 = v6.1 (COMMAND_REBOOT returns to the
//       app; on v6 it leaves the keyboard in update mode)
//   [2] flags: bit 0 controller interface enabled in the settings,
//       bit 1 interface opened by the host, bit 2 controller output on
//   [3] current profile  [4] profile count
//   [5..18] live controller state: buttons (LE16), left trigger, right
//       trigger (0-255), then left X, left Y, right X, right Y (LE int16)
// SET_GAMEPAD_ACTIVE (0x65): [1] 0 = controller output off, 1 = on.
//   Replies like GET_GAMEPAD. Not saved: output is on after every power-up.
// SET_PROFILE (0x66): [1] profile. Switches like the profile keys do (saved,
//   advanced keys reloaded). Replies like GET_GAMEPAD.
#define BOARD_COMMAND_GET_GAMEPAD 0x64
#define BOARD_COMMAND_SET_GAMEPAD_ACTIVE 0x65
#define BOARD_COMMAND_SET_PROFILE 0x66
#define GAMEPAD_PROTOCOL_VERSION 2

static void gamepad_fill_response(uint8_t *out) {
  const xinput_report_t *r = xinput_get_report();
  out[1] = GAMEPAD_PROTOCOL_VERSION;
  out[2] = (uint8_t)((eeconfig->options.xinput_enabled ? 1u : 0u) |
                     (xinput_is_open() ? 2u : 0u) |
                     (xinput_is_active() ? 4u : 0u));
  out[3] = eeconfig->current_profile;
  out[4] = NUM_PROFILES;
  // buttons, lz, rz, joysticks[4]: 14 bytes from offset 2 of the report
  memcpy(out + 5, (const uint8_t *)r + 2, 14);
}

static bool gamepad_command(const uint8_t *in, uint8_t *out) {
  switch (in[0]) {
  case BOARD_COMMAND_GET_GAMEPAD:
    break;
  case BOARD_COMMAND_SET_GAMEPAD_ACTIVE:
    if (in[1] > 1)
      return false;
    xinput_set_active(in[1] == 1);
    break;
  case BOARD_COMMAND_SET_PROFILE:
    if (in[1] >= NUM_PROFILES || !layout_switch_profile(in[1]))
      return false;
    break;
  default:
    return false;
  }
  gamepad_fill_response(out);
  return true;
}

#if defined(BOARD_RGB)
void board_rgb_keycode(uint8_t keycode) { rgb_keycode(keycode); }

void board_host_leds(uint8_t leds) { rgb_host_leds(leds); }
#endif

bool board_command(const uint8_t *in, uint8_t *out) {
  switch (in[0]) {
  case BOARD_COMMAND_GET_GAMEPAD:
  case BOARD_COMMAND_SET_GAMEPAD_ACTIVE:
  case BOARD_COMMAND_SET_PROFILE:
    return gamepad_command(in, out);
  default:
#if defined(BOARD_RGB)
    return rgb_command(in, out);
#else
    return false;
#endif
  }
}

// Called from thread mode by the main loop (see board_task()).
static void failsafe_task(void) {
  if (confirm_pending) {
    confirm_pending = false;
    confirm_build();
    failsafe_armed = false;
  }
  if (usb_fallback_pending) {
    // High speed with the stock PHY setup did not enumerate: restart the USB
    // stack on the plain full-speed transceiver instead.
    // (TinyUSB's DWC2 port has no dcd_deinit, so tud_deinit/tud_init cannot
    // be used; reset the OTG_HS block and re-run the controller init instead.
    // The device stack itself never got past the default state.)
    usb_fallback_pending = false;
    dcd_int_disable(BOARD_TUD_RHPORT);
    dcd_disconnect(BOARD_TUD_RHPORT);
    RCC->AHB1RSTR |= RCC_AHB1RSTR_OTGHRST;
    RCC->AHB1RSTR &= ~RCC_AHB1RSTR_OTGHRST;
    board_usb_init();
    usb_use_hs = false;
    const tusb_rhport_init_t fs_init = {.role = TUSB_ROLE_DEVICE,
                                        .speed = TUSB_SPEED_FULL};
    if (!dcd_init(BOARD_TUD_RHPORT, &fs_init))
      board_vendor_bootloader_jump();
    dcd_int_enable(BOARD_TUD_RHPORT);
    usb_fallback_done = true;
  }
}

// Exception return trampoline: rewrite the stacked PC of a thread-mode frame
// so that returning from the exception lands in the bootloader hand-off
// routine, in thread mode, with a clean xPSR.
static void redirect_frame_to_bootloader(uint32_t *frame) {
  // Stacked PC must be halfword-aligned with bit 0 clear; the Thumb state
  // comes from xPSR.T below.
  frame[6] = (uint32_t)board_vendor_bootloader_jump & ~1u; // PC
  frame[7] = 0x01000000;                             // xPSR: Thumb only
}

// Shared by SysTick and fault handlers. `frame` is the stacked exception frame,
// `exc_return` the EXC_RETURN value the handler was entered with.
__attribute__((used)) void board_exception_redirect(uint32_t *frame,
                                                    uint32_t exc_return) {
  // Bit 3 set: the handler returns to thread mode, so the frame belongs to
  // thread code and can be redirected safely.
  if (exc_return & 0x8) {
    redirect_frame_to_bootloader(frame);
    return;
  }
  // Nested in another handler: set the magic and reset. The vendor bootloader
  // runs first after any reset and sees the same flag as on a direct jump.
  _board_bootloader_flag[0] = BOOTLOADER_MAGIC;
  NVIC_SystemReset();
}

__attribute__((used)) void board_systick(uint32_t *frame, uint32_t exc_return) {
  HAL_IncTick();

  const uint32_t now = HAL_GetTick();
  if (tud_mounted()) {
    if (mounted_since == 0)
      mounted_since = now ? now : 1;
    else if (failsafe_armed && now - mounted_since >= FAILSAFE_CONFIRM_MS)
      confirm_pending = true;
  } else {
    mounted_since = 0;
  }

  if (failsafe_armed && usb_use_hs && !usb_fallback_done &&
      !usb_fallback_pending && !tud_mounted() &&
      now >= FAILSAFE_HS_ATTEMPT_MS) {
    usb_fallback_pending = true;
  }

  if (failsafe_armed && !failsafe_triggered && !tud_mounted() &&
      now >= FAILSAFE_USB_TIMEOUT_MS) {
    failsafe_triggered = true;
    board_exception_redirect(frame, exc_return);
  }
}

// Naked entry points: hand the active stack's frame pointer and EXC_RETURN to C.
#define EXC_ENTRY(name, target)                                                \
  __attribute__((naked)) void name(void) {                                     \
    __asm volatile("tst lr, #4\n"                                              \
                   "ite eq\n"                                                  \
                   "mrseq r0, msp\n"                                           \
                   "mrsne r0, psp\n"                                           \
                   "mov r1, lr\n"                                              \
                   "push {r1, lr}\n"                                           \
                   "bl " #target "\n"                                          \
                   "pop {r1, pc}\n");                                          \
  }

EXC_ENTRY(SysTick_Handler, board_systick)
EXC_ENTRY(HardFault_Handler, board_exception_redirect)
EXC_ENTRY(MemManage_Handler, board_exception_redirect)
EXC_ENTRY(BusFault_Handler, board_exception_redirect)
EXC_ENTRY(UsageFault_Handler, board_exception_redirect)

//--------------------------------------------------------------------+
// Board API
//--------------------------------------------------------------------+

void board_init(void) {
  // The vendor bootloader jumps here with VTOR possibly still pointing at
  // itself; claim the vector table before anything can fault.
  SCB->VTOR = 0x08020000;
  __DSB();

  // The vendor bootloader leaves its 0x96969696 flag at 0x2001FFFC when it
  // starts the app, and stays in update mode on the next reset if the flag is
  // still there. Stock clears it during start-up (v1.06 @ 0x08028644 ->
  // 0x0802BCF8 -> 0x08026A00), so do the same: the app is running.
  _board_bootloader_flag[0] = 0;

  // Start from a clean slate whatever the bootloader left running (its USB
  // core, interrupts, PLL): silence the NVIC, pulse-reset every peripheral
  // the same way the stock firmware does before it hands over, then bring RCC
  // back to its reset configuration so HAL can set the clocks up from HSI.
  __disable_irq();
  SysTick->CTRL = 0;
  for (uint32_t i = 0; i < 8; i++) {
    NVIC->ICER[i] = 0xFFFFFFFF;
    NVIC->ICPR[i] = 0xFFFFFFFF;
  }
  RCC->APB1RSTR = 0xFFFFFFFF;
  RCC->APB1RSTR = 0;
  RCC->APB2RSTR = 0xFFFFFFFF;
  RCC->APB2RSTR = 0;
  RCC->AHB1RSTR = 0xFFFFFFFF;
  RCC->AHB1RSTR = 0;
  RCC->AHB2RSTR = 0xFFFFFFFF;
  RCC->AHB2RSTR = 0;
  __enable_irq();

  HAL_Init();
  if (HAL_RCC_DeInit() != HAL_OK)
    board_error_handler();
  HAL_InitTick(0);

  failsafe_armed = !build_confirmed();

  board_gpio_init();
  board_clock_init();
  board_usb_init();
#if defined(BOARD_RGB)
  rgb_init();
#endif

  // Enable cycle counter
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  DWT->CYCCNT = 0;
}

void board_task(void) {
  failsafe_task();
#if defined(BOARD_RGB)
  rgb_task();
#endif
}

void board_error_handler(void) {
  // Never spin with interrupts off: go back to the vendor bootloader so the
  // keyboard can always be reflashed.
  board_vendor_bootloader_jump();
}

// A restart back into the app, as stock does it (v1.06 @ 0x08026994: clear
// the bootloader flag at 0x08026A0C, then NVIC_SystemReset at 0x08025FC8).
// With the flag left set, the vendor bootloader would stay in update mode.
void board_reset(void) {
  _board_bootloader_flag[0] = 0;
  __DSB();
  NVIC_SystemReset();
}

void board_enter_bootloader(void) { board_vendor_bootloader_jump(); }

uint32_t board_serial(char *buf) {
  const volatile uint8_t *uid = (const volatile uint8_t *)UID_BASE;
  for (uint32_t i = 0; i < 12; i++) {
    buf[i * 2] = M_HEX(uid[i] >> 4);
    buf[i * 2 + 1] = M_HEX(uid[i] & 0x0F);
  }

  return 24;
}

uint32_t board_cycle_count(void) { return DWT->CYCCNT; }

//--------------------------------------------------------------------+
// Interrupt Handlers
//--------------------------------------------------------------------+

void OTG_HS_IRQHandler(void) { tud_int_handler(1); }
