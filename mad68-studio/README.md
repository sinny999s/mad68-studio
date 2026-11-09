# MAD68 Studio

Desktop configurator for the MadLions MAD68 HE running the custom libhmk
firmware from `../mad68-cfw`. It is a fork of
[hmkconf](https://github.com/peppapighs/hmkconf) (the libhmk web configurator)
wrapped in Electron, with MAD68-specific additions.

## Run it

Start `release/MAD68-Studio-0.4.1-portable.exe` (also copied to the Desktop as `MAD68 Studio.exe`). It needs no install.

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
  - **Test** tab: live sticks, triggers and buttons as games receive them,
    read from the keyboard 20 times a second, plus the controller's state
    (off, waiting for Windows, output off, or working).
- **Profiles for Games** (Profiles page, desktop app):
  - List games, picked from the open programs or typed as `game.exe`, each
    with a profile. While one runs, the keyboard switches to its profile, and
    when it closes the profile from before comes back.
  - Switching happens only when a game starts, changes or closes, so a profile
    picked by hand mid-game is kept, and alt-tabbing causes no flash writes.
  - With games listed, closing the window keeps the app running in the tray.
    Starting the app again brings the window back, and the tray menu has Quit.
  - **Make Active** on each profile switches the keyboard to it.
- **Controller toggle keycode** in the Remap picker (Special): `Pad On/Off`
  (`0xDE`), on Fn+G by default.
- **Lighting keycodes** in the Remap picker: `RGB Toggle`, `Next/Prev Effect`,
  `Hue +/-`, `Brightness +/-` and `Speed +/-` (keycodes `0xD5` to `0xDD`).
- **Firmware** (Settings, or the Firmware button on the start screen):
  - Install the bundled custom firmware (v6.1) or restore stock v1.06, or install any
    MAD68 HE RGB upgrade file.
  - Files are checked before anything is erased: IDs, length, trailer checksum,
    stack pointer and entry point.
  - From the custom firmware the app restarts the keyboard into update mode by
    itself. From stock it asks you to unplug, hold Esc and plug back in.
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
