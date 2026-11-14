# MAD68 Studio

Desktop configurator for the MadLions MAD68 HE (RGB, Lightless, Ultra and
Limit) running the custom libhmk firmware from `../mad68-cfw`. It is a fork of
[hmkconf](https://github.com/peppapighs/hmkconf) (the libhmk web configurator)
wrapped in Electron, with MAD68-specific additions.

## Run it

Start `release/MAD68-Studio-0.13.0-portable.exe` (also copied to the Desktop as `MAD68 Studio.exe`). It needs no install.

- Windows SmartScreen may warn because the exe is not code-signed. Choose
  **More info, then Run anyway**.
- The keyboard connects automatically. There is no browser device picker, and
  it reconnects whenever the keyboard is plugged back in.

## What it does

Everything hmkconf does:

- remap keys across layers and profiles
- per-key actuation points and Rapid Trigger
- advanced keys (Null Bind, Dynamic Keystroke, Tap-Hold, Toggle)
- gamepad mode, calibration and settings

Plus MAD68-specific pages:

- **Lighting** (Keyboard Configuration):
  - **Live preview:** an animated on-screen keyboard running the same effect
    maths as the firmware. Typing-driven effects mirror your real key presses
    (polled at about 10 Hz), and you can also click keys.
  - **19 effects:** Solid, Breathing, Rainbow Wave, Rainbow Cycle, Reactive,
    Custom, Key Depth, Ripple, Splash, Starlight, Raindrops, Gradient,
    Heatmap, Dual Breathing and Wave (firmware v5), plus Fire, Digital Rain,
    Spiral and Reactive Cross (firmware v7).
  - From firmware v7, Ripple, Splash, Reactive Cross and Digital Rain go
    further or faster the harder you press, and pressed keys feed Fire. On
    older firmware the page shows only the effects it has and offers the
    update.
  - Colour pickers with a hue/saturation plane, hex input and swatches, plus a
    second colour, speed and direction, shown only when the effect uses them.
  - **Per-key painting** (Custom): drag across keys with a brush, pick up a
    key's colour, or fill all keys or a group (WASD, Arrows, Letters, Numbers,
    Modifiers, Space). The colours are stored on the keyboard.
  - **Presets**: 12 built-in looks, plus your own saved on this computer.
    They include **Matrix** (Digital Rain in green) and **Lights Off**, which
    switches the LEDs off and keeps the effect for later. From firmware v7.1,
    lights off costs the keyboard almost no processing time.
  - **Indicators** (Caps Lock, Fn layer, profile) with their own colour, and
    **idle dimming** from 1 to 60 minutes.
  - With firmware v4 the page shows the 5 original effects and offers the
    firmware update.
- **Gamepad** (firmware v6 for the live parts):
  - **Controller** switch: turning the Xbox controller interface on or off
    restarts the keyboard by itself (about a second) and comes back on the
    same page, instead of asking you to unplug it.
  - **Output** switch, the same as **Fn+G** on the keyboard: controller output
    on or off without restarting. While off, bound keys type normally.
  - **Quick Setup**: WASD stick, Racing (W throttle, S brake, A/D steer) and
    Arrow camera in one click, found by what the keys do on layer 0. Gamepad
    Override is switched on with them, so the keys do not also type.
  - **Opposite Directions** (Analog tab): what the stick does when both keys
    of one direction pair are held, like A and D: **Last Pressed Wins** (the
    newest key takes over, at its own depth, like Null Bind's Last Pressed),
    **Deeper Key Wins** (formerly Snappy Joystick) or **Cancel Out**. Last
    Pressed Wins needs custom firmware v7.2.
  - **Test** tab: live sticks, triggers and buttons as games receive them,
    read from the keyboard 20 times a second, plus the controller's state
    (off, waiting for Windows, output off, or working).
- **Updates:**
  - The Firmware page shows which custom firmware the keyboard runs (v7.2 and
    later report it; older builds show as "v7.1 or older") and which one the
    app installs. When the keyboard's is older, the page offers **Update to
    v7.5**, and a dot on Firmware and a note in the sidebar point to it.
  - The desktop app checks GitHub once per start for a newer MAD68 Studio
    release and links to it. It asks GitHub's public API for the latest
    release of this repository and sends nothing else; offline it stays quiet.
- **Key Scan Speed** (Settings, custom firmware v7.3; the last three speeds
  from v7.4): how often the keyboard reads all of its key sensors. Measured on
  a MAD68 HE, in full scans a second and as sensor readings a second (75 per
  scan):
  - One row of sensors at a time, differing in how long each column is given
    to power up: **Relaxed** (40 µs, v7.2's timing, 1,291, 97K), **Standard**
    (28 µs, 1,688, 127K, about MadLions' own 1,667, the default), **Fast**
    (18 µs, 2,232, 167K) and **Rapid** (8 µs, 3,387, 254K). Quick and Rapid
    were called Fast and Fastest up to MAD68 Studio 0.11.0.
  - Three rows at once, on all three of the chip's converters: **Turbo**
    (8 µs, 4,741, 356K), **Extreme** (5 µs, 6,060, 454K) and **Max** (5 µs
    with shorter sampling, 6,852, 514K). On that keyboard they ran millions
    of scans without a fault and read keys within 0.3% of Relaxed. The
    keyboard checks one of these itself before using it, and runs Rapid
    instead if its readings don't agree; the page says so.
  - The page shows the scan rate the keyboard measures itself, live. Changing
    the speed applies at once, is saved on the keyboard, and makes the
    keyboard re-read every key's resting position.
  - From custom firmware v7.5, Turbo, Extreme and Max read the bottom row
    twice each time and drop a reading that does not agree with its twin.
    That row's sensor line picks up a stray reading about once in 1,900 on
    the MAD68 HE this was measured on; the other rows have none.
- **Key Smoothing** (Settings, custom firmware v7.4): how the key readings are
  smoothed before the keyboard acts on them. **Classic** is the filter up to
  v7.3: it works over time, so it trails the key by about 1.5 ms at any scan
  speed (and more when a fast scan slows the rest of the firmware down).
  **Smooth**, **Balanced** and **Fast** work over the last readings of each
  key instead (8, 4 or 2 of them) and drop a single stray reading, so the
  faster the scan, the less they trail: at Max about 1.2, 0.6 and 0.3 ms. At
  the slower scan speeds they trail more than Classic. Each level shows its
  delay at the current scan speed.
- **Rest Check** (Performance page): a hands-off measurement of how far each
  key reads while nobody is touching the keyboard. It runs for 30 seconds,
  2 minutes or 10 minutes, reads every key's travel as fast as the keyboard
  answers, and then lists the keys that moved, worst first: the deepest
  reading at rest, the key's actuation point in the current profile, the room
  between the two, whether Rapid Trigger is on, and a verdict: fine, close
  (under 0.3 mm of room) or would have pressed. Each key also gets the lowest
  actuation point to try (the deepest reading plus 0.2 mm, never under
  0.2 mm), which a button can set. A key that went past 2.0 mm was pressed by
  hand and is left out. The check changes nothing on the keyboard and only
  covers the time it ran, so a longer check finds rarer movements.
- **Lights off while the PC sleeps** (custom firmware v7.5, no setting): the
  LEDs go dark when the PC sleeps or shuts down with the port still powered,
  and come back as they were when it wakes.
- **Typing pause for calibration** (Calibration page, custom firmware v7.6):
  **Recalibrate** pauses typing, so pressing every key can't type, open the
  Start menu or set off a shortcut. Each key lights up once it has been
  pressed all the way, with a count of how many are done; **Done** brings
  typing back and saves the calibration. **Pause Typing** does the same
  without recalibrating. The keyboard still reads its keys while paused (the
  page shows them live), but nothing reaches the PC. Typing comes back when
  you click Done or Resume Typing, leave the page, or switch to another
  window, and the keyboard ends the pause by itself 3 seconds after the app
  stops asking for it (app closed, crash, cable moved), so it can never be
  left silent. A key held when the pause ends stays silent until it is let
  go. On older firmware the page suggests pressing the keys in an empty
  Notepad window instead.
- **Try Demo** on the start screen opens a MAD68 HE with its real layout and
  default keymap, with simulated typing, so every page can be tried without
  a keyboard.
- **Profiles for Games** (Profiles page, desktop app):
  - List games, picked from the open programs or typed as `game.exe`, each
    with a profile. While one runs, the keyboard switches to its profile, and
    when it closes the profile from before comes back.
  - Switching happens only when a game starts, changes or closes, so a profile
    picked by hand mid-game is kept, and alt-tabbing causes no flash writes.
  - With games listed, closing the window keeps the app running in the tray.
    Starting the app again brings the window back, and the tray menu has Quit.
  - **Make Active** on each profile switches the keyboard to it.
  - **Import** and **Export** on each profile. A file may hold only some
    sections (for example only actuation and advanced keys); the rest of
    the profile is left as it is. A file that can't be used says why: not
    a profile, from a different keyboard, or nothing usable in it.
- **Controller toggle keycode** in the Remap picker (Special): `Pad On/Off`
  (`0xDE`), on Fn+G by default.
- **Lighting keycodes** in the Remap picker: `RGB Toggle`, `Next/Prev Effect`,
  `Hue +/-`, `Brightness +/-` and `Speed +/-` (keycodes `0xD5` to `0xDD`).
- **Firmware** (Settings, or the Firmware button on the start screen):
  - Install the bundled custom firmware (v7.3) or restore stock v1.06, or install any
    MAD68 HE RGB upgrade file.
  - A QMK build for this keyboard (USB ID `373B:1F59`, upgrade files with
    header version `0x0F02`) is recognised too. From QMK the install button
    restarts the keyboard into update mode itself, by sending the QMK build's
    update-mode request (a VIA custom value, `07 00 4D "BOOT"`, on QMK's raw
    HID interface). The keyboard settings pages need the libhmk firmware.
  - Files are checked before anything is erased: IDs, length, trailer checksum,
    stack pointer and entry point.
  - The app restarts the keyboard into update mode by itself, from the custom
    firmware and (from 0.13.0) from MadLions' stock firmware too:
    - **Stock firmware:** it sends the same request as MadLions' updater, a
      32-byte report on the stock raw HID interface (usage page `0xFF60`,
      usage `0x61`), with one byte changed. The updater sends
      `96 96 96 98`, and in stock v1.06 the third `96` first runs stock's
      settings reset. The app sends `96 96 00 98`, which only sets the
      update-mode flag and jumps to the bootloader, so your stock settings
      stay as they are. Checked on all three stock files (Lightless, RGB,
      Ultra/Limit) in the emulator: `../mad68-cfw/tools/stock_update_mode.py`,
      18/18. A mock-WebHID test of the install flow from every stock board
      passes 36/36. **Tested on 2026-11-14** on a MAD68 HE RGB: Restore
      stock (v1.06), then Install custom firmware. The request went to
      `1058 [ff60:61]`, update mode (`2024`) appeared 1.2 s later, and the
      keyboard came back on the custom firmware (v7.6.0) with no manual step.
    - If the keyboard hasn't switched after 10 seconds, the app shows the
      manual ways in: Fn+Ins on the custom firmware, or MadLions' updater on
      stock (close it once the keyboard stops typing). Holding Esc while
      plugging in does not enter update mode on this keyboard.
  - It uses the same bootloader protocol as hub.fgg.com.cn: the "DuckBread"
    sector upgrade (`AA 01` start, `AA 02` erase, `AA 03` data, `AA 96`
    finish), described in `../mad68-cfw/README.md`. The packets it generates
    match a recorded official install, all 774 of them.
  - An install only counts as successful once the keyboard restarts on the
    new firmware. Each step is logged to `%APPDATA%\MAD68 Studio\studio.log`.

## Firmware updates: what is and isn't tested

- 0.1.0 and 0.1.1 used the wrong protocol. The bootloader echoed every
  command and wrote nothing. 0.1.2 uses the official one.
- 0.1.2's packets match all 774 reports of a recorded official install of the
  same file. The mock-bootloader tests pass (16 flasher checks and 9
  install-flow checks). **On 2026-11-09 it installed v4 on the real keyboard.**
  The erase took 489 ms (the official tool: 488 ms), every write was
  acknowledged, and the keyboard restarted on its own 0.7 s after the finish.
  The keyboard was in update mode for about 4 s.
- It only talks to the vendor bootloader, which lives in flash sector 0 and is
  never erased. The worst case is a keyboard stuck in update mode (`373B:2024`,
  no typing). To recover, use the official site's local upgrade with
  a custom firmware file from the Releases page or the stock
  `../firmware/MAD68HE_RGB_v1.06.bin`, or see
  `../mad68-cfw/README.md`, section Recovery.

## Lighting v7 (0.4.0): what is and isn't tested

- **Firmware v7:** 192/192 emulator checks, 9 of them new for these effects.
- **Preview engine:** 8 Node tests check the same behaviour on the app side:
  - the angle maths is identical
  - Fire rises and is fed by keys
  - Digital Rain falls and goes faster from a full press
  - Spiral turns
  - Reactive Cross stays on its lines
  - deeper presses send stronger ripples
- **Demo keyboard:** the effect gallery's new row and the Fire and Digital
  Rain previews.
- **Packaged app on the real keyboard (still v6.1):** the "4 more effects"
  notice.
- **Not yet on hardware:** v7 itself.

## Controller and games (0.3.x): what is and isn't tested

- **On the real keyboard (v6, 2026-11-09):**
  - Windows set up the controller (an XInput HID collection on interface 3).
  - The Test view showed bound keys driving the controller.
  - Turning Controller on or off restarted the keyboard into update mode: see
    "Restarting (v6.1)" in `../mad68-cfw/README.md`.
- **0.3.1:**
  - Bundles v6.1.
  - Restarts the keyboard by itself only when it reports controller protocol 2. On older firmware, both the Controller switch and Settings > Restart
    Keyboard ask for a replug instead.
  - The Test view says when the active profile has no controller bindings, or
    when the page shows another profile than the keyboard uses.

- **Firmware v6:** 178/178 emulator checks, including the controller
  interface opened through TinyUSB, stick, trigger and button output, Fn+G,
  no stuck keys, and the new commands.
- **App:**
  - **Per-game logic:** 10 unit tests cover start, keep, close, game-to-game,
    two games at once, a rule removed mid-game, and program-name clean-up.
  - **Demo keyboard:** Quick Setup (WASD stick, Racing), the Output switch,
    the live Test view and Make Active.
  - **Packaged app on the real keyboard (still v4):** the start screen, the
    running-program list (94 programs seen), the Gamepad and Profiles pages
    with their "needs firmware v6" notices, and the Firmware page.
- **Not yet on hardware:**
  - v6 itself
  - Windows installing the controller
  - the automatic restart-and-reconnect after the Controller switch, which
    says to replug if the keyboard is not back within 12 s
  - an actual game switching profiles
  - the tray icon

## Build from source

```bash
npm install --legacy-peer-deps
```

```bash
npm run app
```

This runs a Vite build, then starts Electron.

```bash
npm run dist
```

This writes the portable exe to `release/`.

Notes for this machine:

- `electron-builder` fails to unpack its signing tools without Windows Developer
  Mode, because the archive contains macOS symlinks. Unpacking
  `winCodeSign-2.6.0` into the electron-builder cache once, without the
  `darwin` folder, fixes it.
- NSIS cannot open files with paths longer than 260 characters. When the
  project sits in a deep folder, build through a short junction, with
  `--preserve-symlinks` so Node keeps the short path:

```powershell
node --preserve-symlinks --preserve-symlinks-main node_modules\electron-builder\cli.js --win portable
```

`MAD68_SMOKE=1` makes the app print the start screen, the number of running
programs it can see, and the Lighting and Firmware pages to stdout, then quit.
`MAD68_SMOKE_TABS=Gamepad,Profiles` picks other pages. It only reads, and is
used as a self-check against the real keyboard.

## Files of interest

| Path                                                 | What                                                                                    |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `electron/main.cjs`                                  | window, `app://` scheme, WebHID permission for VID `0x373B`, running-program list, tray |
| `electron/preload.cjs`                               | `window.studio`: running programs, open windows, keep running in the tray               |
| `src/lib/flasher.ts`                                 | upgrade-file checks and the IAP bootloader protocol                                     |
| `src/lib/components/firmware-tools.svelte`           | firmware install UI                                                                     |
| `src/lib/configurator/lighting/lighting-tab.svelte`  | Lighting page                                                                           |
| `src/lib/libhmk/commands/rgb.ts`                     | GET_RGB / SET_RGB                                                                       |
| `src/lib/keycodes/lighting.ts`                       | lighting keycodes                                                                       |
| `static/firmware/`                                   | bundled firmware (custom v6.1, stock v1.06)                                             |
| `src/lib/libhmk/commands/gamepad-state.ts`           | GET_GAMEPAD / SET_GAMEPAD_ACTIVE / SET_PROFILE                                          |
| `src/lib/configurator/gamepad/`                      | Gamepad page: controller switch, Quick Setup, Test view                                 |
| `src/lib/game-profiles.ts`                           | per-game profile rules and the switching decision                                       |
| `src/lib/configurator/game-profile-watcher.svelte`   | checks running games every 3 s and switches                                             |
| `src/lib/configurator/profiles/game-profiles.svelte` | Profiles for Games section                                                              |
| `src/lib/lighting/`                                  | colour maths, effect list and presets, preview engine                                   |
| `src/lib/configurator/lighting/`                     | Lighting page, live preview, colour picker                                              |

## License

GPL-3.0, like hmkconf and libhmk, by peppapighs. MAD68 additions are under the
same license.
