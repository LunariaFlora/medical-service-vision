# Architecture and protocol notes

This document explains how the two vision channels work, why the boundaries are where they are,
and what the code does when something goes wrong. It is the companion to the code, not a repeat
of the README.

---

## 1. Why two channels instead of one camera

The two codes appear in completely different optical conditions:

| | Route QR | Bedside barcode |
|---|---|---|
| Distance | roughly a metre or more, hand-held sheet at the nursing station | close range, sheet held toward the lens |
| Content | 21x21 module square, high contrast, robust to blur | thin stripes, sensitive to focus, exposure and printer scaling |
| Failure mode | will not decode at all when out of range | decodes intermittently, one or two bad frames in a row |
| Best place to decode | a host CPU with a full-size sensor | a small board next to the code, no image transfer |

Trying to satisfy both with one sensor meant one lens focusing badly for one of the two jobs.
The split also splits the failure surface: a QR problem cannot stall barcode output, and a
barcode problem cannot stall the route decision.

## 2. QR channel on the host

### 2.1 What counts as a result

`host/qr_tcp.py::decode()` asks ZXing-C++ for QR codes in the frame and returns, per code, the
payload and its four corner points. `send_frame()` then applies three filters, in this order:

1. the record must be of type `QR` (a Code128 in the same frame is ignored);
2. `parse_qr()` must accept the payload (one of the four legal values);
3. the payload must not already have been sent **in this frame**.

Filter 3 is per frame, not per session. The same payload seen in the next frame is sent again.
That is deliberate: the receiver owns the decision of whether a repeated line is new
information, and the camera's job is to keep reporting what it currently sees. There is no
"already reported" state to get stuck in.

### 2.2 TCP connection policy

```
state: closed --(first result)---> connecting --ok--> connected
         ^                            |fail
         |                            v
         +--------- cool-down 1 s <---+  (result dropped, not queued)
```

* The connection is created lazily, on the first result that needs it, with a 0.2 s connect
  timeout and `TCP_NODELAY` set.
* A failed send closes the socket, sets a one-second cool-down and returns "not sent". Anything
  that happened during the cool-down is dropped. Nothing is buffered for later delivery — a
  stale route decision delivered late is worse than a missing one.
* `TCP_UNAVAILABLE` goes to the console, never into the payload.

### 2.3 Camera setup and frame loop

The program requests MJPG, 1920x1080, 30 fps, then reads back and prints the actual mode. Two
conditions end the run with an error exit instead of a silent stall: the camera cannot be opened,
and the camera returns no frame. Frames are converted to gray only when the driver hands back
colour; decoding is always on gray.

## 3. Barcode channel on the OpenMV Cam H7 Plus

### 3.1 Sensor setup

`h7_bar_usb.py::run()` and `h7_bar_uart.py::main()` use the same recipe: reset, GRAYSCALE, VGA
(640x480), automatic exposure, automatic gain, `skip_frames(2000)` so the first frames after
power-up are not decoded. `config_h7.py` records these choices as the H7 profile; the shared
`config.py` keeps manual-exposure values for the OpenART bench board and is not the H7 default.

### 3.2 Decode policy

One `find_barcodes()` call per frame, on the unmodified image. No binary-threshold retry, no
resolution sweep, no multi-frame accumulation inside the decoder. The reasons:

* every post-processing variant doubles the time budget on a 480 MHz MCU;
* a threshold that rescues one frame can invent a different payload on another;
* the confirmation layer (section 4) already handles intermittent results, so the decoder stays
  simple and its behaviour stays explainable.

`barcode_scanner.py` still contains the optional binary fallback used by bench demos
(`binary_threshold`), but the production entries pass `None`.

Payload acceptance: `parse_barcode(payload, is_code128, strict_samples)`. In production
`strict_samples=False`, so the only requirements are "non-empty" and "the decoder says Code128".
The six-payload `BAR_PAYLOADS` list is a bench reference for printed samples, not a whitelist on
the deployed path.

### 3.3 USB payload writer

`USBPayloadOutput` is a tiny state machine with three fields: `pending` (bytes still to write),
`offset` (how much of `pending` the device has accepted) and `resync` (a partial line is
outstanding on the host side).

Invariants it maintains:

* **One frame in flight.** `queue()` raises if the previous frame has not been fully written.
  This prevents two frames' payloads interleaving.
* **Line integrity.** A payload containing `\r` or `\n` is rejected with `ValueError` rather than
  being allowed to split into two lines. Injection cannot reach the host as a fake record.
* **Short writes are normal.** `send()` may accept fewer bytes than offered; the offset advances
  by exactly the accepted count, and an invalid count (negative, zero-length read of nothing,
  or larger than what was offered) raises instead of looping.
* **Disconnect drops data.** If the board is unplugged mid-line, `pending` is cleared. If some
  bytes had already gone out, `resync` is set, and the first thing written after reconnection is
  a single `\n`, terminating the partial line the host is holding.
* **Errors stay out of the payload.** `main()` catches everything, turns the red LED on and
  sleeps. A traceback can never be read by the host as a barcode.

### 3.4 Framed result mode

`h7_usb_results.py` reports a JSON result per requested frame inside the RCD1 container, and
`host/result_transport.py` validates it:

* magic and size bounds first, then CRC-32 of the body, so corruption is rejected before parsing;
* shape checks per record (`payload`/`type` strings, `valid` a real bool), per error map, and on
  the optional `barcode_like` appearance hint — a truthy string is rejected, since a sloppy hint
  would silently change how the host treats an empty frame;
* `boot` and `seq` are compared with the previous frame: a changed boot id or a non-increasing
  sequence means the board restarted or replayed a frame, and the run must be restarted instead
  of mixing two sessions;
* a requested original image must arrive as the next RCF1 frame with the same sequence number,
  otherwise both are discarded.

Frame length limits are deliberately tight: results at most 64 KiB of JSON, images exactly
`width * height` GRAY8 bytes with `width <= 640`, `height <= 480`.

### 3.5 Reconnection

`result_transport.frames()` wraps each session. With `reconnect=True` (the deployment entry), an
`OSError`, `ValueError` or `TimeoutError` from a session is reported as a `disconnected` event in
`connection.json` and `connection_events.jsonl`, the mission is told the connection was lost (it
keeps already-read candidates but clears cross-gap clues), any pending snapshot request is
released so a UI button cannot stay stuck, and a new session is attempted after a four-second
wait — long enough for the board's three-second send timeout to expire. Without `reconnect`, the
same error is raised, which is what the diagnostic entry points want.

## 4. Confirmation logic on the board

`result_filter.py` is one instance per code kind, and the two instances never share state.

Each frame becomes at most one of three events:

* `OK` — the payload has been seen `confirm_frames` times, within `max_gap_ms` of the previous
  sighting, and differs from the currently accepted payload (or the accepted payload has been
  gone for `rearm_ms`, so the same value can be reported again after a real gap);
* `REJECT` — there is a decode result but it cannot be used: two codes of the same kind in one
  frame (`multiple_codes_of_same_kind`), or one code of the wrong kind / an unexpected payload.
  Repeats are throttled to one message per second;
* `WAIT` — nothing usable has appeared for `wait_ms`; emitted once, not every frame.

Two design points worth keeping:

* two codes of the same kind never resolve to "probably the first one" — the filter refuses;
* a long gap resets the streak, so two distant glimpses of the same code are not counted as a
  consecutive confirmation.

`CONFIRM_FRAMES = 1` in the current bench config, because both boards were tested with a single
valid frame per code. `test_scan_demo.py` exercises the multi-frame path by patching it to 3, so
stricter settings remain available and tested.

## 5. Two-bed association on the host

`mission.py` turns a stream of barcode observations plus one route payload into two bed results.
It is an **order heuristic**, and it says so in every snapshot (`association: ORDER_HEURISTIC`,
`provisional: true`).

* `set_qr()` accepts exactly one valid route payload while the plan is empty, emits a
  `route_plan` with two stops, and bumps a generation counter.
* Every observation is tagged with the generation captured *before* the frame was requested
  (`capture_token()`); a frame that was in flight across a route change is discarded instead of
  being assigned to the new itinerary.
* All barcode candidates are counted globally (`all_candidates`), so nothing that was seen is
  lost even when it cannot be assigned.
* While collecting for the first bed, the current winner of that bucket needs `STABLE_HITS = 3`
  hits spanning `STABLE_SECONDS = 1.0` s before a *different* payload is treated as the next
  bed's candidate, and the difference must be more than 30 percent edit distance — an off-by-one
  decode of the same box is not a new bed.
* The switch to the second group requires **both** an empty gap (`GAP_FRAMES = 3` frames with no
  barcode over at least `GAP_SECONDS = 2.0` s, and only if the appearance check positively said
  "no stripe pattern") and a stable, clearly different candidate afterwards. Either clue alone is
  not enough.
* `barcode_like=None` means "appearance not checked" and is treated as "not an absence", so a
  missing check can never create a fake gap.
* `connection_lost()` keeps the candidates but clears `gap_ready`, `empty_since` and `pending`.

`LocalInterfaces` writes `interface_events.jsonl` and `mission_state.json` and prints
`LOCAL_ONLY`. It is explicitly a placeholder for the integration boundary: no serial write, no
ROS publish, no motor command.

## 6. Host pipeline

`scan.py` is the general decode path. Its per-frame order is fixed and matters:

1. capture the mission generation token;
2. obtain a frame (file, video frame, RCD1 result, or legacy OpenART debug frame);
3. optional ROI crop;
4. decode (or take the board's records when the board decoded);
5. **append the raw records to `observations.jsonl` before anything filters them**;
6. compute the appearance hint (`barcode_like`) or use the board's;
7. hand the records to the mission, which may accept them;
8. write the full frame record to `frames.jsonl`, save at most one evidence PNG per distinct
   payload signature (and up to 100 total), and update the optional preview.

Timings are recorded per stage (`capture_ms`, `decode_ms`, `mission_ms`, `host_other_ms`, plus
the RCF1 transport breakdown `header_wait_ms`, `payload_receive_ms`, `crc_ms`). The summary
carries a note that capture time includes camera/USB waiting and that the header wait is not pure
exposure time — the numbers are for locating a bottleneck, not for publishing throughput claims.

Evidence frames are the original gray pixels. Overlay rectangles are only ever drawn in the live
window, never into a saved image.

## 7. Failure behaviour

| Condition | Behaviour |
|---|---|
| QR camera will not open, or returns no frame | error message, release, exit code 1 |
| QR TCP receiver refuses or drops | close, 1 s cool-down, result dropped, `TCP_UNAVAILABLE` printed |
| Illegal QR payload | not sent, recorded as `unexpected_qr_payload` |
| Board unplugged mid-line | pending payload dropped, `\n` written after reconnection |
| Payload contains a line delimiter | rejected with `ValueError`, nothing written |
| RCD1 CRC or shape invalid | frame rejected, run ends (deployment: event logged, reconnected) |
| Board rebooted / repeated sequence | run aborted with "start a new run"; candidates not mixed |
| Message printed by the board on startup | host skips up to 4096 bytes looking for the RCF1 magic |
| Two same-kind codes in one frame | `REJECT`, no guess, other kind unaffected |
| Nothing decodable for a while | one `WAIT` event, not a per-frame stream |
| Process started twice on one output directory | OS-level lock fails, second instance exits |

## 8. Verification levels

The tests in `tests/` sit at the lower end of this ladder, and the code comments try to keep the
levels distinct:

1. **Pure logic** — payload parsing, event decisions, framing maths (fully covered by tests).
2. **Real decoder on synthetic images** — ZXing encodes a payload, the same image is decoded back
   (covered).
3. **Transport with doubles or loopback** — TCP against a real local socket; USB against a stub
   that short-writes, disconnects and returns odd lengths (covered).
4. **Generated board bundle in a stub board environment** — proves the merged file behaves like
   its modules (covered).
5. **Real camera on real paper** — not covered here; needs the physical rig.
6. **Real board over USB** — not covered here.
7. **Whole vehicle run** — outside this repository.

Anything at level 5 and above is reported as an observation in the project notes, never as a
"test passed".

## 9. Known limits

* The route mapping table (`11/13/31/33` to beds and boxes) comes from the competition material;
  it is not derived from anything the code can verify.
* `barcode_like()` is a Sobel-density heuristic on the appearance of stripes. It is conservative
  on purpose: it exists to avoid claiming "the code left the frame" when the check simply was not
  conclusive, and it is not a detector.
* The edit-distance rule treats payload strings as opaque text. Real EAN-13 samples differ in
  more than one digit, which is what makes the 30 percent threshold usable, but nothing enforces
  that property.
* `host/barcode_usb.py` decodes barcodes with a USB camera on the host. It works and is tested,
  but it is a prototype: the shipped architecture decodes barcodes on the board.
* `host/openart_transport.py` speaks the OpenART legacy USB debug protocol and is kept only so
  the earlier measurements remain reproducible.
