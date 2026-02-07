# Wiilbalance Toolbar

Wiilbalance Toolbar helps keep your posture straight after long hours at the standing desk! It adds a balance graphic to your toolbar linked, in real-time, to a Wii Balance Board.

## Usage

`uv run python -m app.menubar.py`

## Bluetooth

Wiimote communication happens over Bluetooth L2CAP streams, which Apple killed support for in macOS 12+. This renders it impossible to communicate with wiimotes using the native bluetooth radio built-in to the device. [Bumble](https://github.com/google/bumble) is a userpsace bluetooth driver that sidesteps the OS driver stack that we can use, in combination with an external radio, to establish L2CAP streams.


### Firmware blobs

Bumble uses Linux firmware blobs to initialise adapters. Find some `.bin` files relevant to your device over at the linux firmware repository [https://gitlab.com/kernel-firmware/linux-firmware](https://gitlab.com/kernel-firmware/linux-firmware]) and copy them to the project root.

I've had some luck with a TP-Link UB500 Adapter (firmware blob [rtl8761bu_fw.bin](https://gitlab.com/kernel-firmware/linux-firmware/-/blob/main/rtl_bt/rtl8761bu_fw.bin)). Your mileage may vary.


## Acknowledgements
Special thanks to the [Wiibrew](https://wiibrew.org) wiki and [Wiiuse](https://github.com/wiiuse/wiiuse) library. Without these resources this project would not have been possible.
