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

## Download

Get `MAD68-Studio-<version>-portable.exe` from
[Releases](../../releases). It needs no install. Windows SmartScreen may warn
because the exe is not code-signed: choose **More info**, then **Run anyway**.

To install the custom firmware, open the app and go to **Firmware**, then
**Install custom firmware**. The keyboard restarts by itself in a few seconds.

> **Flashing firmware is at your own risk.** The keyboard's update mode is
> never erased, so a keyboard can always be recovered:
>
> 1. Unplug it, hold Esc, and plug it back in.
> 2. Install the custom firmware again, or use **Restore stock** in the app.

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
