# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# Adds opt-in board hooks to TinyUSB's DWC2 driver (applied to the copy that
# PlatformIO fetched for this environment; idempotent).
#
#   BOARD_DWC2_PHY_SELECT    - board decides HS vs FS at runtime through
#                              `bool board_dwc2_use_highspeed(void)`
#   BOARD_DWC2_FORCE_UTMI    - in HS mode always use the on-chip UTMI+ PHY with
#                              an 8-bit interface, whatever GHWCFG2 reports
#   BOARD_DWC2_KEEP_FS_XCVR  - leave GCCFG.PWRDWN set in HS mode
#
# All three are needed to reproduce the MAD68 HE stock firmware's USB setup
# (GUSBCFG PHYSEL=0, ULPI_UTMI_SEL=0, PHYIF16=0, TRDT=9; GCCFG PWRDWN=1).
# Without the defines the patched code compiles to upstream behaviour.

import os

Import("env")

MARK = "/* mad68-patch */"


def patch(path, old, new):
    with open(path, encoding="utf-8") as f:
        s = f.read()
    if MARK + new in s:
        return
    if old not in s:
        raise RuntimeError(f"patch_tinyusb: anchor not found in {path}:\n{old}")
    s = s.replace(old, MARK + new, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)


libdeps = env.subst("$PROJECT_LIBDEPS_DIR")
root = os.path.join(libdeps, env["PIOENV"], "TinyUSB", "src", "portable", "synopsys", "dwc2")
if not os.path.isdir(root):
    raise RuntimeError(f"patch_tinyusb: TinyUSB not found at {root}")

common = os.path.join(root, "dwc2_common.c")
patch(
    common,
    """  const dwc2_ghwcfg2_t ghwcfg2 = {.value = dwc2->ghwcfg2};
  return ghwcfg2.hs_phy_type != GHWCFG2_HSPHY_NOT_SUPPORTED;
}""",
    """
#if defined(BOARD_DWC2_PHY_SELECT)
  extern bool board_dwc2_use_highspeed(void);
  return board_dwc2_use_highspeed();
#else
  const dwc2_ghwcfg2_t ghwcfg2 = {.value = dwc2->ghwcfg2};
  return ghwcfg2.hs_phy_type != GHWCFG2_HSPHY_NOT_SUPPORTED;
#endif
}""",
)
patch(
    common,
    """  if (CFG_TUSB_MCU != OPT_MCU_AT32F402_405 && // at32f402_405 does not support 16-bit
      ghwcfg4.phy_data_width) {""",
    """
#if defined(BOARD_DWC2_FORCE_UTMI)
  (void)ghwcfg4;
  if (0) {
#else
  if (CFG_TUSB_MCU != OPT_MCU_AT32F402_405 && // at32f402_405 does not support 16-bit
      ghwcfg4.phy_data_width) {
#endif""",
)
patch(
    common,
    """  if (ghwcfg2.hs_phy_type == GHWCFG2_HSPHY_ULPI) {
    TU_LOG(DWC2_COMMON_DEBUG, "Highspeed ULPI PHY init\\r\\n");""",
    """
#if defined(BOARD_DWC2_FORCE_UTMI)
  if (0) {
#else
  if (ghwcfg2.hs_phy_type == GHWCFG2_HSPHY_ULPI) {
#endif
    TU_LOG(DWC2_COMMON_DEBUG, "Highspeed ULPI PHY init\\r\\n");""",
)

stm32 = os.path.join(root, "dwc2_stm32.h")
patch(
    stm32,
    """    // Disable FS PHY, TODO on U5A5 (dwc2 4.11a) 16th bit is 'Host CDP behavior enable'
    dwc2->stm32_gccfg &= ~STM32_GCCFG_PWRDWN;""",
    """
#if defined(BOARD_DWC2_KEEP_FS_XCVR)
    dwc2->stm32_gccfg |= STM32_GCCFG_PWRDWN;
#else
    // Disable FS PHY, TODO on U5A5 (dwc2 4.11a) 16th bit is 'Host CDP behavior enable'
    dwc2->stm32_gccfg &= ~STM32_GCCFG_PWRDWN;
#endif""",
)

dcd = os.path.join(root, "dcd_dwc2.c")
patch(
    dcd,
    """    if (ghwcfg2.hs_phy_type == GHWCFG2_HSPHY_ULPI) {
      dcfg |= DCFG_XCVRDLY;
    }""",
    """
#if defined(BOARD_DWC2_FORCE_UTMI)
    (void)ghwcfg2;
#else
    if (ghwcfg2.hs_phy_type == GHWCFG2_HSPHY_ULPI) {
      dcfg |= DCFG_XCVRDLY;
    }
#endif""",
)

# Clone cores: do not depend on GSNPSID / GHWCFG2 / the ST FIFO size.
#   BOARD_DWC2_SKIP_ID_CHECK  - skip the Synopsys ID assert
#   BOARD_DWC2_CORE_ID        - core revision used to pick the reset sequence
#   BOARD_DWC2_EP_COUNT       - device endpoint count
#   BOARD_DWC2_HS_FIFO_SIZE   - bytes of FIFO RAM TinyUSB may use on the HS core
patch(
    common,
    """  TU_ASSERT(gsnpsid == DWC2_OTG_ID || gsnpsid == DWC2_FS_IOT_ID || gsnpsid == DWC2_HS_IOT_ID);""",
    """
#if defined(BOARD_DWC2_SKIP_ID_CHECK)
  (void)gsnpsid;
#else
  TU_ASSERT(gsnpsid == DWC2_OTG_ID || gsnpsid == DWC2_FS_IOT_ID || gsnpsid == DWC2_HS_IOT_ID);
#endif""",
)
patch(
    common,
    """  // load gsnpsid (it is not readable after reset is asserted)
  const uint32_t gsnpsid = dwc2->gsnpsid;""",
    """
#if defined(BOARD_DWC2_CORE_ID)
  const uint32_t gsnpsid = BOARD_DWC2_CORE_ID;
#else
  // load gsnpsid (it is not readable after reset is asserted)
  const uint32_t gsnpsid = dwc2->gsnpsid;
#endif""",
)
patch(
    dcd,
    """  const dwc2_ghwcfg2_t ghwcfg2 = {.value = dwc2->ghwcfg2};
  return ghwcfg2.num_dev_ep + 1;""",
    """
#if defined(BOARD_DWC2_EP_COUNT)
  (void)dwc2;
  return BOARD_DWC2_EP_COUNT;
#else
  const dwc2_ghwcfg2_t ghwcfg2 = {.value = dwc2->ghwcfg2};
  return ghwcfg2.num_dev_ep + 1;
#endif""",
)
patch(
    stm32,
    """static const dwc2_controller_t _dwc2_controller[] = {""",
    """
#if defined(BOARD_DWC2_HS_FIFO_SIZE)
  #undef EP_FIFO_SIZE_HS
  #define EP_FIFO_SIZE_HS BOARD_DWC2_HS_FIFO_SIZE
#endif
static const dwc2_controller_t _dwc2_controller[] = {""",
)

# With a forced UTMI+ PHY, the MCU-specific PHY init/update must also be told
# UTMI+, not whatever GHWCFG2 reports (a clone may report "no HS PHY", which
# would select the FS transceiver branch and the FS turnaround time).
patch(
    common,
    """  // mcu specific phy init
  dwc2_phy_init(dwc2, ghwcfg2.hs_phy_type);""",
    """
#if defined(BOARD_DWC2_FORCE_UTMI)
  (void)ghwcfg2;
  dwc2_phy_init(dwc2, GHWCFG2_HSPHY_UTMI);
#else
  // mcu specific phy init
  dwc2_phy_init(dwc2, ghwcfg2.hs_phy_type);
#endif""",
)
patch(
    common,
    """  // MCU specific PHY update post reset
  dwc2_phy_update(dwc2, ghwcfg2.hs_phy_type);""",
    """
#if defined(BOARD_DWC2_FORCE_UTMI)
  dwc2_phy_update(dwc2, GHWCFG2_HSPHY_UTMI);
#else
  // MCU specific PHY update post reset
  dwc2_phy_update(dwc2, ghwcfg2.hs_phy_type);
#endif""",
)
print("patch_tinyusb: DWC2 board hooks in place")
