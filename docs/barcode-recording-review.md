# Barcode screen-recording review (first round)

Translated and condensed from the internal engineering note of 2026-10-08. Local file paths and
recording identifiers removed.

## What was evaluated

A 60-second screen recording of the board's camera preview, shot while the preview-only firmware
was running. 2880x1800 at 30 fps, 1801 frames. The recording contains **barcodes only** — the QR
job belongs to the other camera, so nothing here says anything about QR capability.

This is a screen recording, not a sensor dump. The chain under test was therefore
camera optics -> preview scaling -> screen capture -> lossy video encoding -> gray decode, and
none of those stages can be separated afterwards.

## Method

The fixed preview region was cropped out of every frame and decoded with the host's ZXing-C++
build, using the default per-frame settings of `host/scan.py`.

Then a smaller sample was built for closer inspection: one frame every two seconds, keeping only
frames whose pixels were not identical to the previous sample — 31 representative frames covering
different positions in the frame, paper tilt, how much of the frame the code occupied, and motion.
No real distances or angles were measured, so none were invented as labels.

Each of the 31 frames was then decoded in four versions — original crop, crop scaled to 640x480,
original crop binarised at 160, scaled crop binarised at 160 — by both ZXing-C++ and desktop ZBar.
That is 124 engine comparisons; it is **not** 124 independent presentations, because the 31 frames
come from one continuous recording.

## Result

* All 1801 frames: no decode output, program finished normally. Processing took 49.67 s on the
  desktop — an offline processing time, not a frame rate on the vehicle computer.
* All four variants of all 31 sample frames, in both engines: no decode output.
* Earlier host runs had read real payloads from a different saved failure frame, and the
  synthetic QR/Code128/blank controls pass. So "the decoder is broken" is not a valid conclusion
  from this recording, and "switching to the host solves imaging" is not one either.

## What the sample set is and is not

The sample set written to the ignored results directory contains: an HTML contact sheet, a
manifest with source hash, crop coordinates, frame index, timestamp, pixel hash and the reason a
frame was sampled, the cropped gray PNGs (not sharpened, not binarised, not upscaled), and a JSON
of per-variant results for both engines. A contact sheet image is included for orientation only.

It is a **failure regression set**: real frames that currently fail. It is not a labelled
training set, has no ground truth, and has no train/test split. Re-tuning parameters on this same
recording can only be called "an improvement on this recording", never independent generalisation.
Consecutive frames from one recording must not be reported as a success rate.

## Judgement and next step

* The stripe edges in the saved frames are still blurred. The chain that produced them includes
  camera imaging, the IDE preview scaling and the recording compression; this material cannot
  identify which stage dominates the loss.
* Both the full-frame pass and the fixed small-region pass failed, so there is no evidence
  supporting further blind sweeps of exposure or thresholds.
* Keep the camera settings, and move to the **lossless USB GRAY8 path**: let the board send the
  original gray frame and decode it on the host. Back up the board's `main.py`, release the IDE's
  hold on the port, and confirm the USB link end to end before relying on it in a run.
* If original frames still fail, the next thing to examine is the actual lens and the imaging at a
  fixed mounting distance — not more screenshots and not more enlargements of them.

This is why the repository carries a framed original-image path (`wire.py` / `result_transport.py`)
next to the plain payload path: the payload path is enough to run the task, and the image path is
what makes a failing frame diagnosable later.
