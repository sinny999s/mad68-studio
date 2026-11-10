# MAD68 HE custom firmware (libhmk port)

Custom firmware for the **MadLions MAD68 HE RGB** (stock USB ID `373B:1058`,
bootloader `373B:2024`), built on [libhmk](https://github.com/peppapighs/libhmk)
and configured with [hmkconf](https://hmkconf.com) in the browser.

> Status: **v7.2 runs on the real keyboard** and is in daily use: 19 lighting
> effects, the controller (with "last pressed wins" for the stick), restarts
> from the app and the release number MAD68 Studio reads all work, and the
> lighting is not redrawn while it is off or showing a still picture.
> **v7.3** (key scan speed, see below) passes every emulator check and is
> waiting for its first run on the real keyboard.

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

Latest run (`out/MAD68HE_libhmk_v7.3.bin`, log `out/verify_v73.log`): **249/249 passed**.
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
