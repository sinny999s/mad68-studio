# MAD68 Studio

An unofficial desktop configurator and custom firmware for the **MadLions
MAD68 HE**, a 68-key Hall-effect keyboard. It is not affiliated with or
endorsed by MadLions.

- **Analog keys:** per-key actuation points, Rapid Trigger and advanced keys.
  These come from [libhmk](https://github.com/peppapighs/libhmk).
- **Lighting:**
  - 19 effects, including some only a Hall-effect board can do: Key Depth,
    Fire fed by your presses, and ripples that go further the harder you press.
  - Per-key painting, presets, and Caps Lock / Fn / profile indicators.
  - Idle dimming, and a Lights Off preset that costs the keyboard almost no
    processing time.
- **Controller mode:** the keyboard can also act as an Xbox controller, so
  games read how far you press each bound key. Fn+G switches between
  controller and typing without reconnecting, and there is a live test view.
- **Profiles for games:** the app switches the keyboard to a game's profile
  while the game runs, and back when it closes.
- **Safe updates:** firmware installs through the keyboard's own update
  mode. Every build is checked in an emulator before release, and the stock
  firmware is one click away.

## Which keyboards

The original **MAD68 HE** family. MadLions' own firmware for these boards is
one program with a few per-board presets, and it sets up the hardware
identically on all of them (checked by comparing every published firmware
file and running them in the emulator), so the same custom firmware runs on
each:

| Board | USB ID | Status |
|---|---|---|
| MAD68 HE **RGB** | `373B:1058` | Tested, in daily use |
| MAD68 HE **Lightless** | `373B:1057` | Tested by a user on v7.5 ([#1](../../issues/1)) |
| MAD68 HE **Ultra** | `373B:1059` | Supported, not yet tested on a real board |
| MAD68 HE **Limit** | `373B:105A` or `373B:105C` | Supported, not yet tested on a real board |

MAD68 Studio recognises each board and restores the right stock firmware for
it. Your board's USB ID is in Device Manager: Keyboard > Properties >
Details > Hardware Ids.

**Not supported:** the MAD68 HE V2 (a newer platform with different
firmware), and the MAD68 R and MAD68 Pro, which use a different kind of
processor (RISC-V). MAD68 Studio doesn't offer to install on them.

## Download

Get `MAD68-Studio-<version>-portable.exe` from
[Releases](../../releases). It needs no install. Windows SmartScreen may warn
because the exe is not code-signed: choose **More info**, then **Run anyway**.

To install the custom firmware, open the app and go to **Firmware**, then
**Install custom firmware**. The app switches the keyboard into update mode by
itself, and the keyboard restarts on the new firmware in a few seconds.

- **First install, from MadLions' stock firmware:** from MAD68 Studio 0.13.0
  the app sends the stock firmware the same update-mode request as MadLions'
  updater, without the updater's reset of your stock settings. This is checked
  in the emulator on all three stock firmware files and was tested on a real
  MAD68 HE RGB on stock v1.06: it switched to update mode in 1.2 seconds. If
  the keyboard hasn't switched after 10 seconds,
  start an update in MadLions' own updater
  ([hub.fgg.com.cn](https://hub.fgg.com.cn)), close it once the keyboard stops
  typing, and the app carries on. MAD68 Studio 0.12.0 and older always need
  that step.

> **Flashing firmware is at your own risk.** The keyboard's update program is
> never erased, and these are the ways back into update mode:
>
> - **The app:** the install button puts the keyboard into update mode by
>   itself, on this custom firmware and (from MAD68 Studio 0.13.0) on stock
>   firmware. On stock, MadLions' updater is the fallback.
> - **On the keyboard:** with the custom firmware, press Fn+Ins.
> - **Automatic:** if a newly installed custom build cannot connect over USB,
>   it returns to update mode by itself within about 9 seconds.
> - **Interrupted installs:** if an install is cut off, the keyboard stays in
>   update mode (it won't type); run the install again.
>
> Holding Esc while plugging the keyboard in does **not** enter update mode on
> this keyboard.

## What's in this repo

| Folder | What |
|---|---|
| [`mad68-studio/`](mad68-studio) | The desktop app: Electron + SvelteKit, a fork of [hmkconf](https://github.com/peppapighs/hmkconf) |
| [`mad68-cfw/`](mad68-cfw) | The firmware port (libhmk), the tools used to work out the hardware, and the emulator that checks every build |
| [`firmware/`](firmware) | MadLions' stock firmware files, for restoring a keyboard to stock (see the licence note below) |

Build instructions are in each folder's README.

## Contributing

Pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Every pull
request is checked automatically:

- the app: lint, type check and build
- the firmware: build plus the full emulator test suite

The maintainer reviews and merges.

## Credits and licence

- **libhmk and hmkconf** by peppapighs are licensed under GPL-3.0.
- **MAD68 Studio and the MAD68 firmware port** in this repo are also GPL-3.0
  (see [LICENSE](LICENSE)).
- **Stock firmware:** the files in [`firmware/`](firmware) and the bundled
  `mad68-studio/static/firmware/MAD68HE_RGB_v1.06.bin` are MadLions' stock
  firmware, © MadLions / Shenzhen Yizhita Technology Co., Ltd. They are not
  covered by the GPL. They are included only so keyboards can be restored to
  stock.
