# Contributing

Thanks for helping. Bug reports, ideas and pull requests are all welcome.

## How to send a change

1. Fork the repo and create a branch from `main`.
2. Make your change, and run the checks below for the part you touched.
3. Open a pull request against `main`. Describe what changed and how you
   tested it.

GitHub Actions runs the same checks on every pull request. The maintainer
reviews and merges; nobody else pushes to `main`.

## The app (`mad68-studio/`)

```bash
cd mad68-studio
npm ci
npm run lint     # Prettier + ESLint
npm run check    # TypeScript / Svelte
npm run build
npm run app      # run it in Electron
```

`npm run format` fixes formatting. The `/demo` page has a simulated keyboard,
so most of the UI can be worked on without hardware.

## The firmware (`mad68-cfw/`)

A broken firmware build can leave a keyboard in update mode until it is
reflashed, so firmware changes have stricter rules:

- **The emulator suite must pass:**
  `python tools/verify_cfw.py out/MAD68HE_libhmk.elf out/MAD68HE_libhmk.img <arm-none-eabi-nm> .`
  (see `mad68-cfw/README.md` for building). New behaviour needs new checks.
- **Never write flash outside sectors 6 and 7**, and never touch the vendor
  bootloader. The suite checks this.
- **Talk to us first (open an issue) before changing** the USB IDs, the USB
  set-up, the bootloader hand-off or the update protocol.
- **Firmware changes are tested on a real MAD68 HE** before they are merged
  and released.

## Licence

This project is GPL-3.0. By contributing you agree that your contribution is
released under the same licence.
