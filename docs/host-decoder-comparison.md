# Why the host decodes with ZXing-C++: the same-pixel comparison

Translated and condensed from the internal engineering note of 2026-09-28 (local file paths,
attachment identifiers and machine-specific details removed). The measurements are from that
date; they are not a benchmark of the current hardware.

## The question

Barcode frames captured by the board often failed to decode in the board's own decoder. Before
concluding "the camera is bad" or "we need a different decoder", the same image was pushed
through two host-side engines and their behaviour compared **on identical pixels**:

* ZXing-C++, the PyPI binary `zxing-cpp 3.1.1` used by this repository, with the Python binding;
* desktop ZBar, via the `pyzbar 0.1.9` Windows wheel;
* Pillow 12.3.0 for image handling.

The comparison script is `tools/compare_host_decoders.py`. It never opens a camera, never writes
to the board, and copies its inputs verbatim. Images and JSON results belong in ignored
directories.

Two honest caveats from the original note, still valid:

* the inputs were 766x640 **screenshots** supplied by the user, not verified lossless frames from
  the board's sensor;
* desktop ZBar is not the board firmware. A difference between the two host engines says
  something about the algorithms, not about the board.

## Method

Allowed formats were fixed to QR and Code128. No expected-content filter was applied to the
decoder output: whatever the engine returned was recorded.

Each image produced 103 inputs:

* the original RGB image;
* the full frame in gray, in auto-contrast, and sharpened (radius 1, percent 150, threshold 3);
* a hand-selected crop of the code region, in the same three treatments;
* for each of those six, the image itself plus binarised copies at luminance thresholds 80 to 230
  in steps of 10 (luminance >= threshold becomes 255, everything else 0).

No scaling, rotation or perspective correction. A hand-selected ROI in this experiment does not
mean the pipeline can locate the code automatically — it is a bound on what the algorithms can do
with a well-framed code.

Every variant was passed to both engines as the same pixel array; the script verifies the pixel
hash is unchanged before and after decoding. Each variant was decoded three times and only the
median decode time is reported. Frame and variant counts are **not** independent success rates.

## Results

| Input | ZXing-C++ | Desktop ZBar |
|---|---|---|
| A, original RGB / plain gray | nothing | nothing |
| A, full frame gray, threshold 160 | Code128 `6946522463487` | nothing |
| A, full frame gray, threshold 190 | Code128 `6946522463487` | Code128 `6946522463487` |
| A, full frame gray, threshold sweep | readable at 150/160/170/180/190/200 | readable at 170/190 |
| B, original RGB / gray / 160 / 190 and the rest | nothing | nothing |

Median decode time on image A at threshold 160: about 4.69 ms for ZXing and 4.63 ms for ZBar; at
threshold 190 about 4.53 ms and 3.87 ms. Image B in gray: about 7.50 ms and 9.02 ms. These are
offline call times on a desktop CPU. They exclude capture, transfer, preprocessing and display,
so they are not a vehicle frame rate and cannot be compared with the board's decode time.

Control cases (synthetic QR `11`, synthetic Code128, and a blank white image) behaved correctly
in both engines, which confirms the call chain was live and that the negative result was a real
empty result rather than a broken script.

## What the follow-up images added

Three further screenshots (766x640 RGB) were tested with far fewer variants — plain gray plus
thresholds 80 to 230:

* image 1 became readable at threshold 180 in both engines;
* image 2 was readable in ZXing at 120-160, 210 and 220, and in ZBar at 160 and 210;
* image 3 stayed empty in both.

So some frames do contain recoverable information, and the two engines have different tolerance.
That rules out "the sensor produces no data at all", and equally rules out blaming a single
factor such as exposure or defocus, because the threshold at which a frame becomes readable
differs per frame.

A later minimal retest on one stored failure frame used only three inputs (original RGB, gray,
gray at threshold 160). Only ZXing read the payload, and only at threshold 160. Reproducible, and
still not evidence about the board.

## The confusion this cleared up

* A frame that desktop ZBar can read while ZXing cannot (and the reverse) proves that the frame
  **contains decodable information**. It does not prove the board could decode it, and it is not
  a fair comparison with the board's own decoder.
* A frame that both engines fail on does not prove that no algorithm can succeed. It only means
  these candidates failed.
* A screenshot has already been through the IDE's preview scaling and lossy capture. It is not
  the frame the board had in memory.

## Conclusion and what it changed

* There were **local** improvements on some frames, no **robust** improvement across frames.
  Unbounded parameter sweeping was stopped.
* The productive next step was not "more thresholds" but **lossless frames**: let the board keep
  the exact frame it failed on, and let the host decode that, keeping the gray pixels and format
  consistent. That is what the RCD1/RCF1 path in this repository exists to do.
* The strict same-byte A/B — the same stored frame given to both the board's decoder and the host
  decoder — was still not done at the time of writing, and remains an open item.

## Why the host still ends up doing the QR decoding

The measurements above are about recovering hard barcode frames. Separately, the host already
has a Python and C++ binding for the same `ReadBarcodes` interface, a real sensor, and an
independently restartable process. For the long-range QR job that was simply the better place to
run the decoder, and it does not require sending images to a microcontroller. The barcode job
stayed on the board, where the code is close to the lens and no image has to move at all.
