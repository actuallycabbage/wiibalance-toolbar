# Wiilbalance Toolbar

Wiilbalance Toolbar helps keep your posture straight after long hours at the standing desk! It adds a balance graphic to your toolbar linked, in real-time, to a Wii Balance Board.

## Swift toolbar

Run `uv sync`, then `make -C menubar run` to build and launch without opening Xcode.
See [the Swift app guide](menubar/README.md) for settings, tests and architecture, and
[the Python integration checklist](menubar/BACKEND.md) for the backend work needed to
enable adapter selection and pairing controls.

## Troubleshooting

### Adapter identification

You can usually just get away with `usb:0` , but if that doesn't work, the following may be useful with troubleshooting.

`uv run python -m bumble.apps.usb_probe` will probe for usb devices

```
ID 2357:0604
  Bumble Transport Names: usb:0 or usb:2357:0604 or usb:2357:0604/0CEF1518A7E6
  Bus/Device:             001/002
  Class:                  Wireless Controller
  Subclass/Protocol:      1/1 [Bluetooth]
  Serial:                 0CEF1518A7E6
  Manufacturer:
  Product:                TP-Link UB500 Adapter

ID 05E3:0608
  Bumble Transport Names: usb:05E3:0608
  Bus/Device:             001/001
  Class:                  Hub
  Subclass/Protocol:      0/1
  Product:                USB2.0 Hub

```

## Bluetooth

Wiimote communication happens over Bluetooth L2CAP streams, which Apple killed support for in macOS 12+. This renders it impossible to communicate with wiimotes using the bluetooth radio built-in to the device as we cannot side-step the OS's bluetooth stack.

Enter [Bumble](https://github.com/google/bumble), a userspace Bluetooth driver that, in combination with an external radio, we can use to sidestep the OS driver stack to establish L2CAP streams.


### Firmware blobs

Bumble uses Linux firmware blobs to initialise adapters. Find a `.bin` file relevant to your device over at the linux firmware repository [https://gitlab.com/kernel-firmware/linux-firmware](https://gitlab.com/kernel-firmware/linux-firmware]) and copy it over to the project root.

I've had some luck with a TP-Link UB500 Adapter (firmware blob: [rtl8761bu_fw.bin](https://gitlab.com/kernel-firmware/linux-firmware/-/blob/main/rtl_bt/rtl8761bu_fw.bin)). Your mileage may vary.


## Acknowledgements
Special thanks to the [Wiibrew](https://wiibrew.org) wiki and [Wiiuse](https://github.com/wiiuse/wiiuse) library. Without these resources this project would not have been possible.
