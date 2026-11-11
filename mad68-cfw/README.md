# MAD68 HE custom firmware (libhmk port)

Custom firmware for the **MadLions MAD68 HE RGB** (stock USB ID `373B:1058`,
bootloader `373B:2024`), built on [libhmk](https://github.com/peppapighs/libhmk)
and configured with [hmkconf](https://hmkconf.com) in the browser.

> Status: **v7.2 runs on the real keyboard** and is in daily use: 19 lighting
> effects, the controller (with "last pressed wins" for the stick), restarts
> from the app and the release number MAD68 Studio reads all work, and the
> lighting is not redrawn while it is off or showing a still picture.
> **v7.3** (key scan speed, see below) runs on the real keyboard too, with
> its four speeds measured there. **7.3.90**, a test build, found on the real
> keyboard that ADC2 and ADC3 are there and work, and how short the timing
> can get (results below). **v7.4** turns that into three faster scan speeds
> and adds smoothing that runs per reading. It passes every emulator check
> and runs on the real keyboard: every speed and smoothing level without a
> fault or a false press, and a filter delay measured on real presses of
> 0.37 ms at Max with Fast smoothing, against 1.91 ms with the classic filter.

## Other MAD68 boards

Every MAD68-family firmware MadLions publishes (their update server, keyed by
each board's USB IDs as listed in the official web configurator) was
downloaded and compared:

- **Original MAD68 HE family: one firmware, per-board presets.** Lightless
  (`1057`), RGB (`1058`), Ultra (`1059`) and Limit (`105A`, `105C`) run the
  same code: between Lightless and RGB v1.06, 26 of 77,800 bytes differ, and
  Ultra and both Limits get the very same file. What differs is the USB ID,
  a tag on saved settings (`0x115D` / `0x115E` / `0x1162`), one sensor
  threshold (330 on RGB and Ultra, 380 on Lightless) and one default value.
  Booted in the emulator, they leave every GPIO, ADC, DMA, timer, USB, clock
  and LED register identical. Each has its own update-mode ID (`2023`-`2027`),
  but the update program only ever receives the firmware body, so this
  firmware runs on all of them unchanged; MAD68 Studio picks the IDs and the
  stock file per board.
- **MAD68 HE V2 and MAD LIGHT 60HE: a new platform.** Still ARM, but loaded at
  `0x08010000` behind a different update program, with a new codebase (about
  5% of code blocks match the RGB firmware; 49% between the two), and clock
  registers and a GPIO port an STM32F407 doesn't have.
- **MAD68 R and MAD68 Pro: RISC-V.** Their images start with a RISC-V jump and
  a run of `nop`s (the start-up pattern of WCH's CH32V chips), so nothing
  from this firmware applies.

## What was recovered from the stock firmware

All of this came from the official v1.06 image running in an emulator
(`tools/emu.py`, `tools/hwmap.py`, `tools/probe_map*.py`) and from reading the
keyboard's own settings through the stock firmware (read-only, via OpenMAD).

| | |
|---|---|
| MCU | STM32F405/407-compatible Cortex-M4, 168 MHz from a 16 MHz crystal, 128 KB SRAM |
| USB | OTG_HS core on PB14/PB15; stock uses the on-chip UTMI+ HS PHY (GUSBCFG `0x40002400`, GCCFG `0x00210000`) |
| Sensor rows (ADC1) | row0 PA3, row1 PA2, row2 PA1, row3 PA0, row4 PA7 (PB1 and VREFINT are sampled too but are not key rows) |
| Sensor columns (active low) | PA8 PA9 PA10 PA11 PA12 PA15 PC10 PC11 PC12 PD2 PB3 PB4 PB5 PB6 PB7 |
| Matrix | 5 x 15, 68 switches; empty at (2,12) (3,1) (4,3) (4,4) (4,5) (4,7) (4,8) |
| Resting sensor values | 2225-2457 counts (12-bit), measured on this keyboard |
| Debug | SWD on PA13/PA14 stays enabled |

### Flash map

| Sector | Address | Owner | Touched by this firmware? |
|---|---|---|---|
| 0 | `0x08000000` | vendor IAP bootloader | never |
| 1-2 | `0x08004000` | stock firmware settings | never |
| 3-4 | `0x0800C000` | unused/vendor | never |
| 5 | `0x08020000` | the application (stock or this) | programmed only by the vendor bootloader |
| 6 | `0x08040000` | build-confirmation journal (first 64 KB) and RGB settings (second 64 KB) | append-only, erased only if full (latest of both rewritten) |
| 7 | `0x08060000` | libhmk settings (wear leveling) | yes |

The linker script asserts the app fits in sector 5; the flash driver refuses
any erase/write outside sector 7, and the confirmation journal code only
addresses sector 6.

### Upgrade file format

`tools/pack.py` writes the vendor format: 14-byte header (`373B 1058 373B 2024`,
version, big-endian body length), the image, then a big-endian
`0x55555555 - sum(body bytes)` trailer. `pack.py --selftest ../firmware`
re-creates all four official images byte-for-byte.

### Bootloader protocol (from the web configurator)

Enter: unplug, hold **Esc**, plug in (or the firmware's own jump). The device
re-enumerates as `373B:2024` with one HID interface, usage page `0xFF` and
usage `1`. Replugging without flashing returns to the application.

The MAD68 HE uses the configurator's "DuckBread" sector upgrade. This was
confirmed by recording a working official install
(a USB log and a WebHID capture of the official tool). Every command is a
64-byte report, sent as report 0. Byte 63 is the OR of bytes 0 to 62. The
bootloader acknowledges each report by echoing it back.

| Report | Meaning |
|---|---|
| `AA 01 <pages u32 BE> <08 02 00 00>` | start; pages are 2 KB |
| `AA 02 <sectors u32 BE> <08 02 00 00>` | erase 128 KB sectors (about 0.5 s); the host then waits 0.3 s per sector |
| `AA 03 <addr BE> <index> <len> <last> <data <= 54>` | firmware body only (no header or trailer); `index` restarts in each 2 KB page; `last` marks a page's final piece; the echo's byte 63 must match |
| `AA 96` | sent 200 ms after the last data; finish and restart into the application |

An earlier version of this file described a different protocol (`0x80` to
`0x84`), copied from another device family in the same web app. The MAD68
bootloader echoes those commands but ignores them.


## RGB lighting (v3)

Recovered from the stock firmware (v1.06 @ `0x0802BBF0`, `0x08030E60`,
driver table `0x08031E08`):

| | |
|---|---|
| LEDs | 68 WS2812-style, one per key, GRB, MSB first |
| Chain | serpentine: row 0 L->R (0-14), row 1 R->L (15-29), row 2 L->R (30-43), row 3 R->L (44-57), row 4 L->R (58-67) |
| Data pin | PC9 = TIM3_CH4 (AF2) |
| Timing | TIM3 at 84 MHz, PSC 0, ARR 1049; "1" = 67 ticks, "0" = 29 ticks |
| Transfer | DMA1 Stream 2 ch5, circular, 1632 bit slots + 224 zero (latch) slots -> TIM3_CCR4 on every update |
| Brightness | stock gamma curve (`0x08031E18`), brightness capped at 210 -> max 156 per channel |

The firmware (`src/hardware/stm32f407xx_mad/rgb.c`) programs the same
registers with the same values and renders effects into the DMA buffer from
the main loop, 8 LEDs per pass. It starts with your stock look (solid
`#800000` at brightness 194).

| Keys | Action |
|---|---|
| Fn + Tab | lighting on/off |
| Fn + W / Fn + S | next / previous effect (solid, breathing, rainbow wave, rainbow cycle, reactive) |
| Fn + Q / Fn + A | brightness up / down (max = stock max) |
| Fn + E / Fn + D | colour (hue) up / down |
| Fn + R / Fn + F | animation speed up / down |

These keys are added once to the Fn layer of an existing keymap, only where
that position is still transparent. Settings are saved 2 s after the last
change to the second half of flash sector 6.

## Safety design

* **Never touches the bootloader or stock settings** (see flash map).
* **Hands control back to the vendor bootloader** exactly the way the stock
  firmware does (RAM magic `0x96969696` at `0x2001FFFC`, peripheral reset,
  RCC reset values, VTOR/MSP, branch to `0x08000000`), in these cases:
  * **Fn + Ins** (`SP_BOOT`) or the hmkconf "bootloader" command;
  * any fault (HardFault/MemManage/BusFault/UsageFault) or HAL error;
  * on a **trial boot** of a new build: USB not enumerated within ~9 s.
* **Trial boot:** a build is unconfirmed until it has been enumerated for 5 s.
  On a trial boot it first tries high speed with the stock PHY setup, then
  after 4 s plain full speed, then gives up and enters the bootloader. Once
  confirmed (recorded in sector 6 along with the speed that worked) it never
  enters the bootloader on its own, so plugging into a sleeping PC is fine.
* **No hardware watchdog** on purpose: it would keep running inside the vendor
  bootloader and could interrupt a reflash.

## Build

```bash
PIO=/path/to/pio PY=/path/to/python tools/build.sh
```

Output in `out/`: `MAD68HE_libhmk.bin` (flash this), `.elf`, raw `.img`.

## Verification

```bash
python tools/pack.py --selftest ../firmware
python tools/verify_cfw.py out/MAD68HE_libhmk.elf out/MAD68HE_libhmk.img <arm-none-eabi-nm> .
```

`verify_cfw.py` boots the firmware in the emulator with this keyboard's real
resting sensor values and checks GPIO/clock/ADC/USB setup against stock, the
scan order, key presses in both magnet polarities, Fn+Ins, both fail-safe
paths, a HardFault, and that no flash outside sectors 6/7 changes.

Latest run (`out/MAD68HE_libhmk_v7.5.bin`, log `out/verify_v75.log`):
**449/449 passed**: the 375 checks of v7.4 (with the read mode of the three
fast scan speeds now 7), 28 for the lighting while the PC sleeps, 31 for the
paired read and its diagnostics, and 15 for the main loop. v7.4
(`out/MAD68HE_libhmk_v7.4.bin`, log `out/verify_v74.log`) passed its
375. The 7.3.90 test build (`out/MAD68HE_libhmk_v7.4-test1.bin`,
log `out/verify_v74t1.log`) passed the 323 checks of its time, and v7.3
(`out/MAD68HE_libhmk_v7.3.bin`, log `out/verify_v73.log`) its 249; v7.4 passes
all of those unchanged apart from the release number, the new speed count and
the two new commands.
v7.2 (build `0xe59ec365`, log `out/verify_v72.log`) passed the 223 checks of its
time; that suite on a rebuild of v7.1 failed only the new v7.2 stick, protocol
and firmware info checks (`out/verify_v72_baseline_v71.log`), so they really
detect the new behaviour.

On a real keyboard running v7.3 or later (close MAD68 Studio first, as its
traffic on the same raw-HID interface corrupts the readings):

```bash
python tools/scan_check.py            # all four scan speeds, guided key presses
python tools/scan_check.py --speeds 0,3 --idle-only
```

It sets each scan speed, reads the rate the keyboard measures, samples every
key with hands off (noise, deepest reading, false presses), then has you press
Q to P (full-press reading against Relaxed, and crosstalk onto the other keys).
The speed you had is put back at the end. Needs `pip install hidapi`.

Two rules the test harness follows:

- **Raw HID commands are retried while the firmware is busy** (`enqueue`), as a
  host does. The firmware holds one command at a time and frees the slot only
  after the reply has been sent.
- **Lighting checks that compare frames wait for whole new frames**
  (`wait_frames`).
The emulator runs the CPU far slower than the real 168 MHz, so a frame takes
about 50 emulated ms there, against under 1 ms on the keyboard.

- **RGB checks:** stock-identical TIM3/DMA1/PC9 setup and bit encoding, the serpentine LED map, the brightness cap, Fn controls, and the reactive effect.
- **v5 lighting:**
  - every effect renders
  - per-key colours round-trip over USB and show through the stock gamma
  - Key Depth, Ripple and Heatmap respond to simulated presses
  - Caps Lock, Fn-layer and profile indicators
  - idle fade and wake
  - settings and key colours survive a restart
  - v4 settings migrate
  - a full journal compacts with exactly one erase
  - nothing outside sectors 6/7 is ever written
- **v6 controller:**
  - the XInput interface appears only when enabled, on endpoints `0x84`/`0x02`
  - all 4 interfaces open through TinyUSB's drivers, and the FIFOs do not overlap and fit the budget
  - a half-pressed W moves the stick part way, a full press all the way, and W+A gives a round diagonal
  - the trigger and A button work
  - GET_GAMEPAD matches what the host was sent
  - output off releases a held button and lets the key type
  - output on releases a key that was typing (no stuck key)
  - Fn+G toggles output with green/blinking red feedback
  - SET_PROFILE switches profile, releases held input, refuses bad values and skips needless writes
  - Fn+G is added once to older keymaps
- **v6.1 restart:** start-up clears the flag the bootloader leaves, and COMMAND_REBOOT clears it before resetting. Fn+Ins, the safety fallback and crashes still set it, so they still go to update mode.
- **v7 lighting:**
  - Fire is bright at the bottom and tapers to the top, flickers, and a held key feeds it.
  - Digital Rain falls down the pressed key's column, faster from a full press (700 ms) than a light one (780 ms).
  - Spiral colours go round the centre and turn.
  - Reactive Cross lights only the pressed key's row and column.
  - A full press (252/255) sends a stronger ripple than a light one (170/255).
- **v7.1 still frames:**
  - With the lights off, the LEDs are dark and no LED is written again over 18 frames.
  - Caps Lock still shows while off.
  - Solid is drawn once, and a brightness change redraws it.
  - Holding Fn still shows the layer keys.
  - Rainbow Wave still draws every frame.
- **v7.2 stick (last pressed wins):**
  - A held, then D: full left, then full right; D let go: back to full left.
  - D held, then A: left, so the order decides, not the side.
  - A full, then D part way: right, only part way (D's own depth).
  - Both axes follow their own last press (W+A then D: round up-right diagonal; then S: down-right).
  - A touch that stays inside the curve's start dead zone does not take over; pressing past it does.
  - Fn+G off and on with both keys held: centred, then one side fully (no half state).
  - With both options set, last pressed wins takes precedence.
  - Snappy (deeper wins, a full tie points left) and cancel-out behave exactly as before.
- **v7.2 firmware info:** GET_FIRMWARE_INFO reports the release (now 7.3.0), this
  build's ID and the marker string; the marker is in the image exactly once; the
  next unused command (0x6A) is still refused.
- **v7.3 key scan speed:**
  - GET_SCAN reports Standard of 4 speeds and the settle times 40/28/18/8 µs.
  - TIM10 is one-pulse at the chosen settle (28 µs = ARR 4703 at 168 MHz); the
    DMA stream is circular with one interrupt per 5-result sequence; the ADC
    does one sequence per start.
  - Every conversion is started by the TIM10 interrupt, after the column switch
    and the settle timer.
  - The scan rate measured with the cycle counter matches the emulated clock.
  - SET_SCAN applies at once, is saved as a journal record, and re-captures the
    rest values; the same speed again writes nothing; an unknown speed is
    refused.
  - When readings shift after a change, the rest values follow and no key reads
    as pressed; a key held during the capture keeps its rest value.
  - The saved speed is used after a restart.
  - With conversions stopped, the scan is re-armed within 100 ms and carries on
    once they work again, with no key pressed.

The lighting checks that look at a moment in an animation wait for a whole
frame drawn after that moment (`complete_frame_after`). The firmware draws 8
LEDs per main-loop pass, which takes well under a millisecond per frame on the
chip but about 30 ms in the emulator, so reading the LEDs at a fixed time could
land mid-frame and depend on how long the main loop happens to be.

| Area | Result |
|---|---|
| Image | linked for sector 5 only, vector table sane, 38.5 KB of 128 KB |
| GPIO | all 35 registers on ports A-E identical to stock |
| Clocks | PLLCFGR `0x07405410` (identical), 168 MHz, APB1/4, APB2/2, 5 WS |
| ADC | rows PA3/PA2/PA1/PA0/PA7, 28-cycle sampling, PCLK2/4, as stock |
| USB | stock's vendor PHY-block writes (`+0x200/204/210/224`) and core presets in stock order; UTMI+ HS PHY 8-bit, TRDT 9, GCCFG `0x00210000`; works with the core's ID/capability registers reading 0; FIFOs within 1280 bytes |
| Scan | one column powered at a time, order PA8..PB7, baselines = real keyboard |
| Keys | no key pressed at rest; press/release works for both magnet polarities |
| Fn+Ins | hands over to the vendor bootloader exactly like stock |
| Trial boot, no USB | full-speed retry at 4 s, bootloader at 9 s |
| Confirmed boot, no USB | stays in the app (12 s tested), keeps the speed that worked |
| HardFault | hands over to the vendor bootloader |
| Flash | nothing outside sectors 6/7 ever erased or written |

Bugs the suite caught and that are fixed in this build: TinyUSB never
enabling high speed on STM32F4, libhmk's downward-only rest calibration
leaving high-resting sensors (W) pressed, `tud_deinit` being a no-op on DWC2
(fallback now re-inits the controller directly), the HID wait loop starving
the fail-safe bookkeeping, a 3-cycle ADC sample time inherited from the F446
port, and a stacked-PC Thumb bit that real hardware would have rejected.

**First hardware test (v1) failed:** the keyboard ran the firmware but never appeared on USB, and
the fail-safe returned it to update mode as designed. Cause: the MCU is an STM32F4-compatible
part whose high-speed PHY is enabled through a vendor register block at OTG_HS+0x200 that
TinyUSB knows nothing about, and TinyUSB also depended on ID/capability registers a clone may
not report like ST. v2 reproduces stock's USB bring-up exactly and stops relying on those.

## What emulation cannot prove

* **USB on the real chip.** The PHY setup copies stock register-for-register,
  with a full-speed fallback and the bootloader as last resort, but actual
  enumeration only happens on hardware.
* **Sensor settle time and press direction/travel.** The driver learns each
  key's polarity and rest value itself; actuation depth may need tuning in
  hmkconf after the first real test (bottom-out calibration is automatic).
* **That the vendor bootloader keeps honouring the RAM magic** when entered
  from this firmware. It is the same sequence the stock firmware uses.
* **That Windows installs the controller (v6)** from this descriptor at high
  speed, and that a plain reset (`COMMAND_REBOOT`, used to apply the setting)
  passes straight through the vendor bootloader back to the app. The
  descriptor and endpoints are libhmk's own; the reset path is the one the
  bootloader takes after every upgrade.

## Lighting (v5)

### Effects

The effect numbers are the values of `mode`. Effects 0-4 are unchanged from
v4, so saved v4 settings keep their meaning.

| # | Effect | Uses |
|---|---|---|
| 0 | Solid | colour |
| 1 | Breathing | colour, speed |
| 2 | Rainbow Wave | speed, direction |
| 3 | Rainbow Cycle | speed |
| 4 | Reactive | colour (dim base), colour 2 (flash), speed |
| 5 | Custom | one stored colour per key |
| 6 | Key Depth | key travel 0-255 blends colour to colour 2 and sets brightness, with afterglow |
| 7 | Ripple | rings of colour 2 spread from each press over a dim colour; they travel 60-300 units depending on how deep the key went |
| 8 | Splash | rainbow rings from each press, as far as Ripple's |
| 9 | Starlight | twinkles of colour 2 over a dim colour |
| 10 | Raindrops | random keys in random colours |
| 11 | Gradient | colour to colour 2 along a direction |
| 12 | Heatmap | each press warms its key from blue to red; it cools at a rate set by the speed |
| 13 | Dual Breathing | crossfade between colour and colour 2 |
| 14 | Wave | a band of colour sweeps over a dim colour 2 |
| 15 | Fire | heat rises from sparks on the bottom row and cools on the way up, on a 15 x 5 grid; colour sets the flame's base hue; pressed keys add heat equal to their travel |
| 16 | Digital Rain | one streak per grid column falls with a bright head and a fading tail of colour; a press drops one from the key, faster the deeper it goes |
| 17 | Spiral | hue by angle around the centre plus distance from it, turning with the speed |
| 18 | Reactive Cross | like Ripple, but the pulse runs only along the key's row and column |

**Still frames (v7.1):** with the lighting off, or on Solid, Custom or
Gradient, a frame is drawn once. The DMA keeps sending it to the LEDs, and it
is only drawn again when something on it changes:

- the settings
- the per-key colours
- Caps Lock or the profile indicator
- the Fn layer
- the idle fade

Measured in the emulator, lighting off falls from about 2,350 to about 650
lighting instructions per main-loop pass. On the keyboard, only the
once-per-frame bookkeeping is left (about 25 µs every 16.7 ms). Animated
effects are unchanged.

Ripples and Digital Rain streaks follow their key's travel for 250 ms after
the press, because a key counts as pressed at the actuation point, before it
reaches its deepest.

Spatial effects use each LED's key centre on a 224 x 64 grid. `gen_keyboard.py`
writes it as `RGB_LED_X` / `RGB_LED_Y` from the key widths. The directions are:
left to right, right to left, top to bottom, bottom to top, centre out, and
edges in.

### Indicators and idle

Indicators draw over the effect in their own colour (white by default):

- **Caps Lock** lights the key bound to Caps Lock while the host reports Caps
  Lock on. libhmk now passes the keyboard interface's LED output report to
  `board_host_leds()`. It shows even with the lighting off.
- **Fn layer**: while a layer above 0 is held, keys bound on it light up and
  the rest dim to 1/6.
- **Profile**: for 1.5 s after a profile switch, the number key of the new
  profile lights up.

**Idle dimming** fades the lighting out after 1 to 120 minutes without key
travel, at 0 by default. Any key brings it straight back.

### Raw HID commands

libhmk's `commands.c` passes any unknown command to `board_command()`. Full
layouts are in `hardware/stm32f407xx_mad/rgb.h`.

| Command | Request | Reply |
|---|---|---|
| `0x60` GET_RGB | none | on, effect, hue, sat, brightness, speed, effect count (19), max brightness (210), LED count (68), protocol version (2), colour 2 hue and sat, direction, idle minutes, indicator flags, indicator hue and sat, direction count (6), key count (68) |
| `0x61` SET_RGB | on, effect, hue, sat, brightness, speed; then optionally `2` followed by colour 2, direction, idle, indicators | same as GET_RGB |
| `0x62` GET_RGB_KEYS | first key | first key, count (up to 20), R G B per key |
| `0x63` SET_RGB_KEYS | first key, count (1 to 20), R G B per key | same as GET_RGB_KEYS |

SET_RGB rejects the following, and changes nothing:

- an effect number of 19 or more
- a direction of 6 or more
- an idle time over 120 minutes

Brightness is capped at 210. Without the `2` marker only the six v4 fields
change, so v0.1 apps cannot reset the new settings. Firmware v4 reported
protocol 1 and does not know `0x62`/`0x63`.

### Storage

The RGB half of flash sector 6 (`0x08050000`, 64 KB) is an append-only
journal of typed records:

- Layout: `B7, type, length (LE16), payload, CRC-32 (LE)`, padded to 8 bytes.
- Type 1 is the 16-byte settings record; type 2 holds R G B for the 68 keys.
- Only the newest record of each type is CRC-checked. The whole journal is
  re-checked only if one of those fails, for example after a torn write.
- Records are saved 2 s after the last change.

v4's last 8-byte `A5` record is still read at boot, and its settings are
re-saved as a type 1 record. When the journal is full, sector 6 is erased
once and the build confirmation plus the newest record of each type are
written back.

## Restarting (v6.1)

The vendor bootloader leaves `0x96969696` at `0x2001FFFC` when it starts the
app. If the next reset finds it still there, the bootloader stays in update
mode, which also catches an app that never got going. Stock clears it in two
places (v1.06):

- during start-up (`0x08028644` -> `0x0802BCF8` -> `0x08026A00`)
- right before each of its own resets (`0x08026994`: `0x08026A0C`, then
  `NVIC_SystemReset` at `0x08025FC8`)

Up to v6 this firmware did neither. So `COMMAND_REBOOT` (hmkconf's "Restart
Keyboard", and MAD68 Studio's automatic restart after the Controller switch)
always ended in update mode, and only unplugging got the keyboard back. v6.1
clears the flag in both places, as stock does.

GET_GAMEPAD reports protocol 2 from v6.1, so MAD68 Studio only restarts the
keyboard by itself on firmware that comes back. On older firmware it asks for
a replug instead.

## Controller (v6)

libhmk can already present the keyboard as an Xbox 360 controller (XInput)
with analog sticks and triggers driven by key travel. v6 checks that this fits
the MAD68's USB core, and adds the pieces that make it practical in games.

### USB

The controller interface (`FF/5D/01`) uses endpoints `0x84` IN and `0x02` OUT,
32 bytes each, polled every microframe. With the controller on, the device has
4 interfaces on endpoints 1-4, inside the 6 endpoints stock initialises. The
FIFOs use 132 of the 320 words (1280 bytes) set aside: RX 60, then 16 for each
64-byte IN endpoint and 8 for the controller. The emulator opens every
interface through TinyUSB's own drivers to check this. Windows only sees an
added or removed controller after the keyboard reconnects. MAD68 Studio
restarts the keyboard for the user (`COMMAND_REBOOT`).

### Fn+G: controller output on and off

`SP_GAMEPAD_TOG` (`0xDE`) switches controller output on and off at run time,
without reconnecting. It is on Fn+G by default, and builds before v6 get it
added once to Fn+G where that key is still transparent.

- **Off:** keys bound to the controller type as normal keys, and the
  controller reports centred sticks and no buttons.
- **On:** the bindings, curve and override work as before.
- Output starts on after every power-up and is not saved.
- **Switching releases everything held.** A controller button held across the
  switch is released, and so is a key that was typing when output switched on.
  Upstream skipped the key instead of releasing it, which could leave it stuck.
  Switching profile also releases held controller input, because the new
  profile has its own bindings.
- **Lighting feedback** (with the profile indicator on): the bound keys, or the
  toggle key if nothing is bound, light green for 1 s when output switches on,
  and blink red/dark when it switches off, so it shows over a red effect.

### Raw HID commands

| Command | Request | Reply |
|---|---|---|
| `0x64` GET_GAMEPAD | none | version (1 = v6, 2 = v6.1, 3 = v7.2); flags: bit 0 interface enabled, bit 1 opened by the host, bit 2 output on; current profile; profile count; then the live state: buttons (LE16), left and right trigger (0-255), left X/Y and right X/Y (LE int16) |
| `0x65` SET_GAMEPAD_ACTIVE | `0` off / `1` on | same as GET_GAMEPAD |
| `0x66` SET_PROFILE | profile | same as GET_GAMEPAD, after switching as the profile keys do |
| `0x67` GET_FIRMWARE_INFO (v7.2) | none | major, minor, patch of the custom firmware release; build ID (LE32); the marker string (`MAD68CFW v7.3.0`), zero-padded to 24 bytes |
| `0x68` GET_SCAN (v7.3) | none | see [Key scan speed](#key-scan-speed-v73) |
| `0x69` SET_SCAN (v7.3) | speed | same as GET_SCAN, after applying and saving the speed |

Invalid values are refused and change nothing. SET_PROFILE to the profile that
is already active writes no flash. libhmk's own `FIRMWARE_VERSION` stays at
upstream's protocol number (v1.8) so hmkconf keeps working; the custom release
is only in GET_FIRMWARE_INFO and in the marker, which MAD68 Studio reads from
upgrade files to show which release a file holds. Firmware before v6 answers these as unknown
commands. MAD68 Studio uses them for the live controller view and for switching
profiles per game.

### Opposite directions on the stick (v7.2)

When both keys of one stick axis are held (A and D, or W and S), one of three
rules decides what the stick does. It is set per profile in the gamepad
options byte:

| Bits | Rule | Stick |
|---|---|---|
| neither | Cancel out | the two depths subtract, so two full presses centre it |
| bit 3 `snappy_joystick` | Deeper key wins | the deeper key's direction and depth; a full tie points left or down |
| bit 4 `last_joystick` (v7.2) | Last pressed wins | the key pressed most recently, at its own depth; letting it go hands back to the other key if that one is still held |

A key counts as pressed for this once it is past the start dead zone of the
analog curve, so a light touch that doesn't move the stick doesn't take over
either. The press order is tracked every scan and forgotten when controller
output is switched off or on. Bit 4 takes precedence over bit 3. MAD68 Studio
sets both for "Last Pressed Wins", so firmware before v7.2 (which ignores bit 4)
falls back to deeper key wins. Studio offers the option only when GET_GAMEPAD
reports version 3.

## Key scan speed (v7.3)

(Speed names: MAD68 Studio 0.11.1 calls speeds 2 and 3 **Quick** and
**Rapid**; this README, written earlier, calls them Fast and Fastest. The
firmware itself only knows the numbers 0-6.)

The Hall sensors are read one column at a time: power the column, wait for the
sensors to settle, convert the five rows (5 × 40 ADC cycles at 21 MHz =
9.5 µs), move to the next of the 15 columns.

**How the stock firmware does it** (v1.06, traced in the emulator): ADC1 runs
continuously over 7 channels (vref, PB1, then the 5 rows) into a circular
21-sample DMA buffer, and every transfer-complete interrupt switches to the
next column. So each column gets exactly 40 µs, 600 µs per scan, about 1,670
full scans per second, and each key's value is the average of the three passes
in that window, including samples taken within a few µs of powering the column.

**v7.2** waited 40 µs and only then converted, plus a few µs of HAL calls per
column: about 50 µs per column, roughly 1,300 scans per second, about 25%
slower than stock.

**v7.3** makes the wait a setting and does the per-column work on registers:
TIM10 in one-pulse mode times the settle, its interrupt starts the ADC
sequence, and a circular DMA stream raises one interrupt per column, which
stores the results, switches the column and starts TIM10 again. A late
interrupt can only lengthen a settle, never shorten it.

| Speed | Settle | Per column (measured) | Full scans/s (measured) |
| --- | --- | --- | --- |
| 0 Relaxed | 40 µs | 51.6 µs | 1,291 (v7.2's timing) |
| 1 Standard (default) | 28 µs | 39.5 µs | 1,688 (stock: 1,667) |
| 2 Fast | 18 µs | 29.9 µs | 2,232 |
| 3 Fastest | 8 µs | 19.7 µs | 3,387 |

The keyboard measures its real rate with the cycle counter over every 64 scans
and reports it; the figures above are from a MAD68 HE, and show about 2.3 µs of
interrupt work per column on top of the settle and the 9.5 µs of conversions.
Standard reads each row at least 28 µs after the column is powered, later than
the earliest samples stock already averages in.

**Hardware check** (`tools/scan_check.py`, same keyboard): with nobody touching
the keys, no speed produced a false press and Fastest was no noisier than
Relaxed (worst key 13-14 counts peak-to-peak against 15-22). Bottomed-out
presses of Q to O read 0.997-1.022× their Relaxed values at Fastest, so 8 µs
is enough settle on this board, and the next-column neighbor of a held key
read 0 mm at both speeds (no carry-over between columns). The bottom row
(ADC row 4) reads up to about 0.4 mm at rest at every speed, which is
harmless at the default 2 mm actuation point but matters below about 0.5 mm.

- **Saved** as a type-3 record (`[speed, 0, 0, 0]`) in the sector 6 journal,
  next to the lighting records. Firmware before v7.3 skips it.
- **After a change** every key's rest value is captured again at the new
  timing (16 scans; the old values stay in use meanwhile), because readings
  can shift slightly with the settle time. A key whose reading moved more than
  300 counts from its old rest value is being held, and keeps the old one.
- **Self-repair:** an ADC overrun, a DMA error, or no column finishing for
  100 ms power-cycles the ADC (abandoning any sequence in progress), re-arms
  the DMA stream and converts the current column again after a full settle.

GET_SCAN (`0x68`) reply: scan protocol version (1); current speed; speed count;
measured full scans per second (LE16, 0 while measuring); 1 while rest values
are being captured; then the settle time in µs of each speed. SET_SCAN (`0x69`)
takes the speed, applies it, saves it and replies the same way. Firmware before
v7.3 answers both as unknown commands; MAD68 Studio then shows the setting as
needing v7.3.

## Faster scan speeds and per-reading smoothing (v7.4)

### Scan speeds 4-6

Three more scan speeds, read with all three ADCs at once and with the next
column powered before the results are stored (how: "Timing test" below).
Measured on a MAD68 HE:

| Speed | Settle | Sample | Full scans/s | Sensor readings/s |
| --- | --- | --- | --- | --- |
| 3 Fastest (one ADC, v7.3) | 8 µs | 28 cycles | 3,372 | 253K |
| 4 Turbo | 8 µs | 28 cycles | 4,741 | 356K |
| 5 Extreme | 5 µs | 28 cycles | 6,060 | 454K |
| 6 Max | 5 µs | 15 cycles | 6,852 | 514K |

Turbo changes nothing about how a sensor is read: same settle and sample
time as Fastest. A three-minute run of each on the keyboard (hands off, about
0.85, 1.09 and 1.23 million full scans, log `out/scan_soak_v74t1.json`) had
no fault and no false press.

**A three-ADC speed is a request.** The chip is an STM32F4-compatible part
and this firmware runs on other MAD68 boards too, so the keyboard never
assumes ADC2 and ADC3 work:

- It always starts on one ADC (a three-ADC speed starts as Fastest), captures
  the rest values, and only then asks for the saved speed's timing.
- Asking is a probe: 16 scans at that timing with the keys left on their
  readings. If no key's average is more than 150 counts from its reading
  just before, the keyboard switches, moving each key's rest value by as much
  as its reading moved, so no key's travel changes at that instant.
- Otherwise the speed is refused: it stays the saved speed, the keyboard
  runs as Fastest, and GET_SCAN says so. The same happens later after 32
  faults (overrun, DMA error, a result of ADC2/ADC3 missing) or if no column
  finishes for 100 ms. Setting the speed again retries.

The saved record is still `[speed, ...]`; v7.3 meeting a speed it does not
know starts on Standard.

### Smoothing

libhmk's filter is `(x + 15y) / 16` once per main loop pass, on whatever the
latest reading is. Its delay is therefore about 15 passes whatever the scan
speed: about 1.5 ms at 10,000 passes a second, and more on a fast scan, whose
interrupts slow the main loop (7,300 passes a second at Max: 2 ms). A faster
scan alone does not make keys respond sooner.

v7.4 adds three levels that take one step per new reading of a key instead
(`matrix.c`, enabled by `MATRIX_SCAN_FILTER`; the analog driver counts the
readings of each column):

1. the median of the key's last three readings, which drops a single stray
   reading completely;
2. `y += (median - y) / 8`, `/ 4` or `/ 2` (Smooth, Balanced, Fast), in
   1/256 counts, once per reading taken since the last pass;
3. the result follows that down at once, and up only from 8 counts below it
   (`MATRIX_FILTER_RISE_BAND`). A resting key so sits at the low edge of its
   noise, where the start-up calibration put its rest value, as it does with
   the classic filter (whose integer rounding gives it a band of 16).

| Level | Trails the key by | At Max (6,852 scans/s) | At Fastest (3,372) |
| --- | --- | --- | --- |
| 0 Classic (default) | about 15 main loop passes | about 2.0 ms | about 1.5 ms |
| 1 Smooth | 8 readings | 1.2 ms | 2.4 ms |
| 2 Balanced | 4 readings | 0.6 ms | 1.2 ms |
| 3 Fast | 2 readings | 0.3 ms | 0.6 ms |

The stray readings matter on this board: the bottom row (PA7) has one 30 to
90 counts off several times a second at every timing. Classic lets about a
fifth of each through (in the emulator one 60-count stray reading moves the
filtered value 12 counts, 0.3 mm of travel near the top); levels 1-3 show
none of it.

Changing level moves nothing: the new filter starts from the filtered value
in use. Start-up calibration runs with the saved level. The level is saved as
byte 1 of the scan record (`[speed, level, 0, 0]`), which v7.3 ignores.

### Commands

GET_SCAN (`0x68`) is at protocol version 2: after the settle times come each
speed's mode and sample code, whether the saved speed is in effect (0),
switching (1) or refused (2), the smoothing level and count, and the main
loop rate. SET_SCAN (`0x69`) with a three-ADC speed replies while it is still
switching. SET_SCAN_FILTER (`0x6C`) sets the smoothing level. Layouts:
`firmware/hardware/stm32f407xx_mad/scan.h`.

SCAN_STATUS (`0x6B`) can also time the filter on real presses: for each
press, the time from the key's reading passing half travel to its filtered
value doing so (count, average, shortest, longest).

**On the keyboard:** `tools/speed_test.py` (close MAD68 Studio first) uses
every combination of the four fastest speeds and the four levels for a few
seconds with hands off (scan rate, main loop rate, faults, how much a resting
key's filtered reading moves, false presses), then has you tap keys at five
settings and reports the delay the keyboard measured.

### Results on a MAD68 HE (v7.4, 2026-11-11)

`tools/speed_test.py`, log `out/speed_test_v74.json`. Every speed came into
effect (none refused). Over the 16 combinations of Fastest, Turbo, Extreme
and Max with the four levels, 5 s each with hands off: no fault, no false
press, and no key read deeper than 0.00 mm.

| Speed | Full scans/s | Main loop/s, Classic | Main loop/s, levels 1-3 |
| --- | --- | --- | --- |
| Fastest | 3,354 | 8,678 | 8,705 |
| Turbo | 4,715 | 7,741 | 6,610 |
| Extreme | 5,990 | 6,962 | 5,463 |
| Max | 6,770 | 6,626 | 5,090 |

(While the script reads every key over USB; idle, Max with Fast runs at about
7,000 scans and 6,000 passes a second.) The per-reading filter costs main
loop time once most passes have a new reading for every key, and at Max the
loop misses about a quarter of the readings; the filter takes their steps
anyway, so its delay in time holds, as measured below.

At rest the filtered reading of a key moved by (highest minus lowest, in
counts, median key / worst key over the four speeds): Classic 4-6 / 11-17,
always worst on a bottom-row key; levels 1-3: 0-1 / 1-4, with one exception
of 12 on a bottom-row key in one 5 s window (Extreme, Balanced), which is what
two stray readings in a row look like. The per-reading levels are steadier at
rest than the classic filter.

Filter delay on real presses at Max (34 to 43 presses per level), from a
key's reading passing half travel to its filtered value doing so:

| Level | Measured | Shortest - longest | Expected |
| --- | --- | --- | --- |
| 0 Classic | 1.91 ms | 1.50 - 2.40 ms | 2.11 ms |
| 1 Smooth | 1.06 ms | 0.87 - 1.25 ms | 1.15 ms |
| 2 Balanced | 0.60 ms | 0.45 - 0.70 ms | 0.57 ms |
| 3 Fast | 0.37 ms | 0.17 - 0.70 ms | 0.29 ms |

Fastest with Classic (v7.3's fastest setting) was not captured in that run;
by the same rule that predicts Classic at Max within 10%, it is about 1.5 ms.

## Timing test (from 7.3.90, the v7.4 test build; still in v7.4)

The 7.3.90 build was for finding out how fast this board can read its keys.
It started up and read the keys exactly as v7.3 does, and added two raw HID
commands that try other timings. v7.4 keeps them. Nothing they do is saved:
after a restart the keyboard is back on its saved scan speed.

**What can be varied** (a "timing"):

- **Settle time**, in timer ticks (168 per µs), down to a fraction of a µs.
- **ADC sample time**: 3, 15 or 28 cycles and up (28 is stock's and v7.3's).
- **Three ADCs at once.** The row pins reach more than one ADC (PA0-PA3:
  ADC1, ADC2 and ADC3; PA7: ADC1 and ADC2), so ADC1 can convert rows 0 and 3,
  ADC2 rows 1 and 4 and ADC3 row 2 at the same time: two conversion rounds
  (3.8 µs at 28 cycles) instead of five (9.5 µs). Each ADC has its own
  circular DMA stream (DMA2 stream 0, 2 and 1); the timer interrupt starts
  ADC3, ADC2, then ADC1, so ADC1 finishes last and its DMA interrupt still
  ends the column, after checking that the other two delivered.
- **Next column first.** The interrupt that ends a column can take the
  results out of the DMA buffer, power the next column and start its settle
  time, and only then store the results. In the emulator the work a column
  has to wait for drops from 110 instructions (v7.3) to 71 with one ADC and
  93 with three.

**Probes.** The MCU is an STM32F4-compatible part and the stock firmware only
ever uses ADC1, so none of this is taken for granted. A probe (`SCAN_PROBE`,
`0x6A`) runs the scan at a timing for 16 to 4,096 full scans and keeps each
key's lowest, highest and average raw reading and the time the scans took,
while the keys stay on their readings from before it. Whatever a timing does
to the readings, it cannot press or release a key. `SCAN_STATUS` (`0x6B`)
returns the state, the fault counters, the main loop rate and the results.
Layouts are in `firmware/hardware/stm32f407xx_mad/scan.h`.

**Going live.** A probe can ask for its timing to be kept. That only happens
if, after 16 scans, no key's average is more than 150 counts from its reading
just before; each key's rest value then moves by as much as its reading did,
so no key's travel changes at that instant, and every rest value is captured
again. A live timing is given up for the saved scan speed after 32 faults
(overrun, DMA error, results of ADC2/ADC3 missing) or if no column finishes
for 100 ms, and whenever a scan speed is set. ADC2 and ADC3 are only clocked
once a probe asks for them; if their registers do not answer the probe fails
and nothing is switched.

**On the keyboard:** `tools/scan_probe.py` (close MAD68 Studio first).

```bash
python tools/scan_probe.py                  # every timing: hands off, then keys held down
python tools/scan_probe.py --live 3,8,2     # read the keys with one timing for 20 s
```

The first form compares every timing with the reference (one ADC, 40 µs,
28 cycles): scan rate, noise, how far the rest readings moved, how a full
press reads, and whether keys around a held one read differently. The second
runs one timing for real and counts false presses and faults.

**What emulation cannot prove here:** that this chip has an ADC2 and ADC3
that behave like ST's, that its DMA request mapping for them matches the
STM32F407's, and how short a settle or sample time its sensors and ADC
inputs tolerate. That is what the probes measure.

### Results on a MAD68 HE (2026-11-10)

`tools/scan_probe.py`, 512 scans per timing with hands off and 128 with 2, R,
J, M and Space held down (one key per row). Logs: `out/scan_probe_v74t1.json`,
`out/scan_live_v74t1.json`. ADC2 and ADC3 are present and work, with no fault
(overrun, DMA error, missing result, stalled scan) in any probe or live run.

Probes, against the reference (one ADC, 40 µs, 28 cycles). "+" = next column
first. Rest moved = worst row's median; full press = range over the five keys.

| Timing | Scans/s | Total | Rest moved | Full press |
| --- | --- | --- | --- | --- |
| 1 ADC, 8 µs, 28 cyc (Fastest) | 3,365 | 252K | -1.1 | 99.8-100.1% |
| 1 ADC+, 8 µs, 28 cyc | 3,473 | 260K | -1.1 | 99.8-100.1% |
| 1 ADC+, 5 µs, 28 cyc | 4,122 | 309K | -2.6 | 99.8-100.0% |
| 3 ADCs, 8 µs, 28 cyc | 4,497 | 337K | +1.2 | 99.9-100.1% |
| 3 ADCs+, 8 µs, 28 cyc | 4,815 | 361K | +1.2 | 99.9-100.2% |
| 3 ADCs+, 5 µs, 28 cyc | 6,171 | 463K | -1.6 | 99.8-100.3% |
| 3 ADCs+, 4 µs, 28 cyc | 6,818 | 511K | -3.0 | 99.7-100.2% |
| 3 ADCs+, 3 µs, 28 cyc | 7,630 | 572K | -6.1 | 99.5-99.9% |
| 3 ADCs+, 2 µs, 28 cyc | 8,661 | 650K | -16.0 | 98.4-99.8% |
| 3 ADCs+, 1 µs, 28 cyc | 9,988 | 749K | -44.6 | 94.9-99.6% |
| 3 ADCs+, 5 µs, 15 cyc | 6,994 | 525K | -2.4 | 99.8-100.3% |
| 3 ADCs+, 5 µs, 3 cyc | 7,989 | 599K | -4.0 | 99.7-100.4% |

- **Settle time is the limit.** A row read less than about 3 µs after its
  column is powered reads low, at rest and pressed, by about three times as
  much for every microsecond less (-6, -16, -45 counts at 3, 2, 1 µs; a full
  press is about 760 counts). Only the rows converted first are affected.
  At 5 µs the readings are within 3 counts and 0.3% of the reference.
- **Sample time is not**, down to 15 cycles. At 3 cycles the second row an
  ADC converts moves towards the first by a few counts.
- The three ADCs agree to within 2.4 counts at rest and 0.3% over a full
  press. Median noise is 8 counts peak to peak at every timing down to 3 µs.
- No key next to, or in the column of, a held key moved more than 2 counts
  more than at the reference timing.
- The bottom row (PA7) shows occasional readings 30 to 90 counts off at every
  timing, the reference included. They are not caused by the timing.

Live, 8 s each with hands off (while the script reads all keys over USB,
which costs the main loop about 12%; idle it runs at 10,763 passes a second
on one ADC at 8 µs):

| Timing | Scans/s | Total | Main loop/s | Faults | False presses |
| --- | --- | --- | --- | --- | --- |
| 1 ADC, 8 µs, 28 cyc (Fastest) | 3,372 | 253K | 9,448 | 0 | none |
| 3 ADCs+, 8 µs, 28 cyc | 4,741 | 356K | 8,253 | 0 | none |
| 3 ADCs+, 5 µs, 28 cyc | 6,060 | 454K | 7,751 | 0 | none |
| 3 ADCs+, 5 µs, 15 cyc | 6,852 | 514K | 7,328 | 0 | none |
| 3 ADCs+, 5 µs, 3 cyc | 7,813 | 586K | 6,909 | 0 | none |

**What this means for response time.** The scan interrupts take time from the
main loop, so the loop slows as the scan speeds up, and above about 7,000
scans a second the loop no longer sees every scan. libhmk's filter
(`matrix.c`: `(x + 15y) / 16`) runs once per main loop pass, not once per
scan, so a key's filtered travel trails its real travel by about 15 passes:
about 1.5 ms today, and more when the loop is slower. A faster scan alone
therefore does not make keys respond sooner. It does deliver more fresh
readings per millisecond, which a filter that runs per scan could turn into
the same smoothing over a shorter time.

## Recovery

1. If the keyboard comes up as `373B:2024` (no typing), it is in the vendor
   bootloader: flash `firmware/MAD68HE_RGB_v1.06.bin` (stock) or a fixed build
   through hub.fgg.com.cn's manual upgrade, or just replug to retry the app.
2. If it does nothing at all, press Fn+Ins (custom firmware), or start the
   update from MadLions' updater (stock firmware). Holding Esc while plugging
   in does **not** enter update mode on this keyboard.
3. Last resort: an ST-Link on the SWD pads (PA13/PA14) can reflash anything.

Your stock settings are untouched in flash sectors 1-2 and come back as soon as
the stock firmware is flashed again; `backup/stock-settings.json` also has
them, restorable with OpenMAD's `tools/restore.py`.

## Layout

```
mad68-cfw/
  backup/      stock settings + resting sensor samples read from the keyboard
  firmware/    libhmk with the stm32f407xx_mad driver and keyboards/mad68he
  out/         build output
  tools/       emulator, hardware-map probes, packer, verifier, generators
```

## Lighting while the PC is asleep (v7.5)

When the PC goes to sleep, or is shut down with the USB port still powered,
the LEDs go dark. When it wakes they come back as they were. There is no
setting for this.

**How it is detected.** A sleeping PC stops sending anything on USB. After
3 ms of silence the USB core reports the bus as suspended, and TinyUSB keeps
that state (`tud_suspended()`). The LED driver (`rgb.c`) looks at it once per
frame time (16 ms), between frames, so the main loop pays nothing for it. The
LEDs go dark only if the host had configured the keyboard before the bus went
quiet (`tud_mounted()`). At start-up and on a charger the bus is quiet too,
but nobody has talked to the keyboard yet, so the lighting stays on as before.

**Going dark** writes one all-off frame into the LED buffer. After that no
frame is worked out or drawn; the DMA stream keeps sending the dark frame.
Key scanning carries on, and a key press still asks the PC to wake up if the
PC allows that (`hid.c`, unchanged).

**Coming back** needs no stored state: the frames simply run again. Nothing
in the settings is changed and nothing is written to flash, so the effect,
colours, brightness and indicators are the same as before. Any of these
brings the lighting back:

- the PC resumes the bus (woken by its power button, the mouse or a timer);
- the PC answers this keyboard's wake-up request after a key press;
- the PC resets the bus without resuming it first, which is what it does when
  it is switched on from shut-down. TinyUSB has no callback for that, which is
  why the driver reads the bus state instead of using `tud_suspend_cb()` and
  `tud_resume_cb()`.

**Idle dimming** carries on where it was. Time asleep does not count as time
without typing: lighting that was on comes back on, and lighting that had
already faded out stays out until a key is pressed. The key that wakes the
PC is such a key press, so it also brings the lighting back.

**The fail-safe is unchanged**, and `board.c` is not touched. A suspended
keyboard still counts as mounted, so a new build that is put to sleep before
its 5 s confirmation is neither restarted at full speed nor sent back to the
bootloader, and it is confirmed on time. A new build that never enumerates
still goes back to the vendor bootloader after 9 s, whatever the bus looks
like.

Section 22 of `verify_cfw.py` checks all of this in the emulator against a
model of the host: the bus reset, SET_ADDRESS, SET_CONFIGURATION and
SET_FEATURE (remote wake-up) arrive through the USB core's registers and run
through TinyUSB's own code, as do the suspend, resume and SOF interrupts. The
emulator also now leaves interrupts pending while the firmware has them
disabled, as the chip does; before, it could interrupt such a stretch, and
one timing-test check passed or failed with the exact instruction count of
the build.

What the emulator cannot show, and the keyboard has to:

- that this chip's USB core raises the suspend interrupt when the PC sleeps,
  and the resume interrupt (WKUPINT) when the PC is woken by something other
  than this keyboard. If that one never came, the LEDs would stay dark after
  waking the PC with the mouse or the power button, until a key is pressed;
- what the PC does to the bus around sleep, hibernate, shut-down and a
  restart, and when Windows takes over from the BIOS. If the bus goes quiet
  for a moment after the keyboard was configured, the LEDs go dark for that
  moment.

Checked by hand on a MAD68 HE (2026-11-11): the LEDs went dark when the PC was
put to sleep and came back when it woke. Which of the ways above woke it was
not noted, and hibernate, shut-down and a restart were not tried.

Only the LEDs are switched off. The MCU, the sensors and the scan keep running
as before, so the keyboard still draws far more than the 2.5 mA a suspended
USB device is allowed, as it did before v7.5.

## Stray readings on the bottom row (v7.5)

### What was measured (v7.4, 2026-11-11)

`tools/rest_log.py` ran for 37 minutes with nobody touching the keyboard:
about 8.5 million scans, as back-to-back 256-scan probes, in eleven
conditions (log: `out/rest_log_v74.jsonl`).

- **Only the bottom row (sensor line PA7: Left Ctrl to Right) has stray
  readings.** About 1 reading in 1,900 is more than 20 counts away from the
  key's average, which is about 35 a second over the row at Max. The other
  four rows had none in the whole run.
- **Nothing the firmware does when reading changes the rate.** It was the
  same with one ADC and with three, with the next column powered before or
  after the results are stored, with sample times from 3 to 480 cycles, settle
  times from 5 to 40 us, scan rates from 1,165 to 7,016 a second, and with the
  LEDs on or off.
- **They look like a short burst of interference on the line**, not like
  noise: 20 to 60 counts in size with a sharp limit near 55 and most of them
  close to that limit, 60% upward, and on all ten keys of the row alike. Two
  readings in a row were stray about once every 53 s over the row, which is
  what chance gives. (Counted per 256-scan probe they looked random in time;
  v7.5's log, below, shows that they are not.)
- **Nothing drifts.** Every key's average moved between -0.1 and +0.5 counts
  in 37 minutes, so the rest values need no tracking while the keyboard is
  on. (The warm-up after a cold start was not measured.)

Smoothing levels 1-3 already drop a single stray reading (the median of
three). The classic filter lets each one through at a sixteenth of its size.

### The bottom row is read twice (timing mode bit 2)

From v7.5 the three-ADC scan speeds (Turbo, Extreme, Max) use timing mode 7
instead of 3. ADC2 converts PA7 twice in a row for each column, while ADC1
converts PA3 and PA0 and ADC3 converts PA2 and PA1. Every ADC then does two
conversions per column, so the scan is as fast as before.

The two readings of a bottom-row key are 1.3 us apart at Max
(`pair_reading()` in `analog.c`):

- within 16 counts of each other (`SCAN_PAIR_AGREE`): the key's reading is
  their average;
- further apart: one of them is stray. The one closer to the key's last
  reading is used, and the pair is counted (`SCAN_STATUS` reply bytes
  49-52).

So a stray reading is dropped before the smoothing and the keys see it, at
every smoothing level. A disturbance that lasts through both readings and
moves them the same way still gets through; such a pair is logged (below) if
it is more than 24 counts (`SCAN_PAIR_JUMP`) from the key's last reading.

Mode bit 2 needs bit 0 (three ADCs). Modes 5 and 7 can be probed and run live
like the others (`tools/scan_probe.py`). The one-ADC speeds (Relaxed to
Fastest) read as in v7.4.

### Finding the source (test protocol 3)

Four more `SCAN_STATUS` (0x6B) requests, layouts in `scan.h`. None of them
saves anything.

| Request | What it does |
| --- | --- |
| 4 | **Stray reading log**: the last 64 entries, three per reply. Each has the cycle counter when the column's results arrived, the cycle counter at the last USB start of frame, the SysTick counter, the key, both readings relative to the key's last one, and a kind: 1 = the pair disagreed, 2 = it agreed but jumped, 0 = an ordinary pair, logged once in 2,048 for comparison |
| 5 | **Pin activity**: watches one port's input levels for 20 ms and reports how often each pin changed and how long it was high |
| 6 | **Pin override**: analog mode, input, pull-up or pull-down for one of the unused pins next to the sensor lines (PA4, PA5, PA6, PC4, PC5, PB0; never an output), or back to what the stock firmware leaves |
| 7 | **USB frame timestamps** on or off: the start-of-frame interrupt notes the cycle counter 8,000 times a second, for the log |

`tools/stray_hunt.py` uses them (about seven minutes, hands off): how often
the pairs disagree with the USB link quiet and busy, whether any stray
reading still reaches a key, which pins change level by themselves, when the
stray readings fall within the USB microframe and the millisecond tick
compared with ordinary readings, and whether a pull or analog mode on a
neighbouring pin changes the rate. The stock firmware leaves PA4, PA5 and PA6
on an alternate function with no pull and nothing driving them, and PA6 is
next to PA7.

### Main loop

At Max the main loop ran slower than the scan (about 6,000 passes a second
against 7,000 scans), so a pass did not see every reading on its own. Two
changes make a pass cheaper when little is happening:

- `matrix_scan()` (per-reading smoothing levels only): a key with no new
  reading is not fetched at all, and a key whose filtered value did not
  change is left alone, because its travel and state would come out as they
  went in. One pass per millisecond still goes through every key, which picks
  up a changed profile, actuation point or Rapid Trigger setting.
- `layout_task()`: a key that is up and was up is skipped (unless controller
  bindings are reading its travel).

In the emulator, with the scan at Max's rate and every key at rest, a pass
went from 12,851 to 9,836 instructions.

Section 23 of `verify_cfw.py` checks the paired read, the log and the pin
diagnostics; sections 20 and 21 check the scan speeds with mode 7. Section 24
holds a key part of the way down, moves its actuation point below and above
that with and without Rapid Trigger, and checks that the key and the layout
follow within 3 ms although its filtered value never changes, at Max with
Fast smoothing, at Fastest with Smooth, and at Max with Classic.

### Results on a MAD68 HE (v7.5, 2026-11-11)

`tools/stray_hunt.py`, hands off, saved speed Max with Fast smoothing (logs:
`out/stray_hunt_v75_a.json`, `out/stray_hunt_v75_b.json`).

**The fast scan speeds with the bottom row read twice** (20 s each, live):

| Speed | Scans/s | Readings/s | Main loop passes/s | Faults | v7.4 scans/s |
| --- | --- | --- | --- | --- | --- |
| Turbo | 4,710 | 353K | 10,991 | 0 | 4,741 |
| Extreme | 5,997 | 450K | 8,171 | 0 | 6,060 |
| Max | 6,744 | 506K | 6,747 | 0 | 6,852 |

The paired read costs about 1% of the scan rate (more interrupt work per
column). The main loop now makes at least one pass per scan at every speed; at
Max it was about 6,000 passes a second on v7.4.

**Stray readings reaching a key** (probes of 256 scans, counted per
bottom-row key, 20 s per mode, run twice):

| Read mode | Probes of a key with a stray reading |
| --- | --- |
| 3: each row read once | 619 of 4,630 and 578 of 4,630 (13%) |
| 7: bottom row read twice | 0 of 4,600 and 0 of 4,600 |

The two readings of a pair disagreed 71 to 76 times a second (1 pair in about
900), the same with the USB link quiet or busy. In 60 s of the log, 4,337
pairs disagreed: in every one of them only one reading was off (the other was
within 9 counts of the key's last reading), and no pair agreed on a jump.

**The source is the LED data signal**, on one stretch of its way through the
LEDs:

- The stray readings repeat with the LED data frame: 1,856 bit slots of
  12.5 us = 23.2 ms. Folded against that frame, 4,335 of 4,337 fall inside one
  block of 168 slots, which is exactly the data of 7 LEDs; the other 91% of
  the frame has none. Inside the block they are spread evenly over the 7 LEDs
  and over the 24 bits of each, and 1 pair in 84 has one.
- Within a 12.5 us bit slot, the upward ones sit in a 0.4 us window and the
  downward ones 0.35 to 0.8 us after it: the rising and the falling edge of
  the data pulse (a 0 bit is high for 0.35 us, a 1 bit for 0.8 us).
- Folded against the USB microframe (125 us) and the millisecond tick they
  are spread as evenly as ordinary readings are.

Each LED keeps the first 24 bits it sees and passes the rest on, so the
stretch of data line between LED n and LED n+1 only carries pulses while the
data of the LEDs after n goes by. A block of exactly the last 7 LEDs' data
means the pulses that disturb PA7 are the ones on the line from LED 60 to
LED 61: from Left Alt's LED to the Space bar's LED, the long hop under the
space bar. (The LEDs are numbered in key order along the bottom row; the last
seven are Space, Right Alt, Fn, Right Ctrl, Left, Down, Right.) The edges
there are driven by Left Alt's LED, not by the MCU, so nothing about the
MCU's own LED pin (PC9) changes them. Turning the lighting off does not stop
them either: an all-off frame is still sent, as 0 bits.

This also explains the 37-minute log: the same rate at every scan timing
(the sampling instant lands on an edge by chance), and the same with the
lights off.

**Pins** (not the source): apart from the fifteen column lines and the LED
data pin, no pin of ports A to E changes level. PA4 rests high, PA5 and PA6
low. With a pull-down, pull-up or analog mode on PA6, PA5, PA4, and analog
mode on PC4, PC5, PB0, the rate stayed between 70 and 76 a second (72 as
stock leaves them, +-2).

Not part of this test: the one-ADC scan speeds (Relaxed to Fastest), which
still read the bottom row once. There, smoothing levels 1 to 3 drop a stray
reading (the median of three) and Classic lets it through at a sixteenth of
its size, as in v7.4. The lighting while the PC is asleep was checked by hand
(see that section).
