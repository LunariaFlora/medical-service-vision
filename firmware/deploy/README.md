# Prebuilt board bundles

Every file here is **generated** by `tools/build_standalone.py` from the sources in
`firmware/`. Do not edit them by hand: edit the sources and rebuild, otherwise the
next build will silently overwrite your changes.

They exist so that flashing does not require Python on the operator's machine.

| File | Target board | Transport | What it is |
|---|---|---|---|
| `h7_bar_usb.py` | H7 | USB virtual COM (payload lines) | The firmware used with the TCP/line receiver: one decode per frame, writes `payload + "\r\n"`. **Rename to `main.py` on the board for normal operation.** |
| `h7_usb_results.py` | H7 | USB virtual COM (RCD1 frames) | Result frames with metadata instead of plain payload lines. |
| `h7_scan_uart.py` | H7 | UART | Bench bundle with the UART output module compiled in. |
| `h7_scan_test.py` | H7 | none | Bench test on the H7 with no output transport: both decoders run on the same frame. |
| `openmv_scan_test.py` | OpenMV-class | none | Bench test: QR and Code128 on the same frame, no output transport. |
| `openmv_scan_uart.py` | OpenMV-class | UART | Same bench flow with UART output. |
| `openmv_scan_demo.py` | OpenMV-class | none | Older demo flow kept for comparison. |

## Flashing

1. Back up whatever `main.py` already sits on the board's USB drive.
2. Copy the file you need in as `main.py`.
3. Eject safely, then power-cycle.
4. Use a cable that carries data. A charge-only cable, or a USB-to-TTL adapter, will not work
   because results travel over the board's own virtual COM port.

The host program must not also be running the board code, and the IDE must not hold the
serial port while the business program is reading it.

## Rebuilding

```sh
python3 tools/build_standalone.py
python3 -c "import sys; sys.path.insert(0,'tools'); import build_standalone as b; print(b.build_usb_results(payload_usb=True))"
```

The first command writes the bench and RCD1 bundles, the second the payload-line firmware.
Both write into `build/`, which is not tracked; copy the result here if you want it in the
repository.
