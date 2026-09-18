"""Computer use for the smith: AT-SPI observes any app on the desktop as a structured tree,
Jev picks the action, uinput performs it with a real pointer/keyboard. No screenshots in the
common path (Jev is text-only); no browser ports; you watch it happen in your own windows.

    atspi.py    observe: apps → windows → actionable elements with desktop coords
    uinput.py   act: absolute-pointer mouse + keyboard over /dev/uinput (Wayland-safe)
    agent.py    the loop
"""
