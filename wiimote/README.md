# Wiimote
A [Bumble](https://github.com/google/bumble) based wiimote interface.

## macOS 12, Bluetooth L2CAP and Bumble
Wiimote communication happens over Bluetooth L2CAP streams, which Apple killed support for in macOS 12+. This renders it impossible to communicate with wiimotes using the native bluetooth radio. [Bumble](https://github.com/google/bumble) is a userpsace bluetooth driver that sidesteps the OS driver stack that we can use, in combination with an external radio, to establish L2CAP streams.

## Thanks
Special thanks to the [Wiibrew](https://wiibrew.org) wiki and [Wiiuse](https://github.com/wiiuse/wiiuse) library. Without these resources this project would not have been possible.
