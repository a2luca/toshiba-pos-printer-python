#!/usr/bin/env python3
"""
Toshiba 6145 — Full Feature Demo
Prints a receipt that exercises every capability of toshiba_6145_driver.py.
Run as root (libusb needs raw USB access):
    sudo nix-shell -p python3 python3Packages.pyusb python3Packages.pillow python3Packages.qrcode \
        --run "python3 examples/demo.py"
Or with pip:
    pip install pyusb pillow qrcode
    sudo python3 examples/demo.py
"""

import sys
import os
import math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from toshiba_6145_driver import (
    Toshiba6145,
    BARCODE_EAN13, BARCODE_CODE128, BARCODE_CODE39,
    QR_EC_L, QR_EC_M, QR_EC_H,
    PRINT_MODE_BOLD, PRINT_MODE_DOUBLE_HIGH, PRINT_MODE_DOUBLE_WIDE,
)
from PIL import Image, ImageDraw, ImageFont


def make_test_image(width=400, height=200) -> Image.Image:
    """Generate a sample image: diagonal stripes + text label."""
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    # Diagonal stripes
    for x in range(0, width + height, 20):
        draw.line([(x, 0), (x - height, height)], fill="black", width=3)

    # White box with text in the centre
    box = [width // 4, height // 4, 3 * width // 4, 3 * height // 4]
    draw.rectangle(box, fill="white", outline="black", width=2)
    draw.text((width // 2, height // 2), "TOSHIBA 6145", fill="black", anchor="mm")

    return img


def separator(p, char="=", width=42):
    p.print_line(char * width)


def main():
    print("Connecting to printer …")
    p = Toshiba6145()
    p.connect()

    status = p.get_status()
    print(f"  Status byte: {status:#04x}  online={p.is_online()}  paper_empty={p.is_paper_empty()}")
    if not p.is_online():
        print("  WARNING: printer reports offline")

    try:
        # ----------------------------------------------------------------
        # Header
        # ----------------------------------------------------------------
        p.set_alignment("center")
        p.set_print_mode(bold=True, double_wide=True, double_high=True)
        p.print_line("FEATURE DEMO")
        p.set_print_mode()

        p.set_print_mode(bold=True)
        p.print_line("Toshiba TCx 6145-1TN")
        p.set_print_mode()

        p.print_line("Pure Python USB Driver")
        p.print_line("IBM SureMark Protocol")
        separator(p)

        # ----------------------------------------------------------------
        # Text formatting
        # ----------------------------------------------------------------
        p.set_alignment("left")
        p.set_print_mode(bold=True, underline=True)
        p.print_line("1. TEXT FORMATTING")
        p.set_print_mode()

        p.print_line("Normal text (CP1252)")
        p.set_bold(True);      p.print_line("Bold text");           p.set_bold(False)
        p.set_underline(True); p.print_line("Underlined text");     p.set_underline(False)
        p.set_invert(True);    p.print_line("Inverted (white/blk)"); p.set_invert(False)

        p.set_double_wide(True)
        p.print_line("Double wide")
        p.set_double_wide(False)

        p.set_double_high(True)
        p.print_line("Double high")
        p.set_double_high(False)

        p.set_print_mode(double_wide=True, double_high=True)
        p.print_line("2x2 size")
        p.set_print_mode()

        # Alignment
        p.set_alignment("left");   p.print_line("<-- Left aligned")
        p.set_alignment("center"); p.print_line("-- Centre --")
        p.set_alignment("right");  p.print_line("Right aligned -->")
        p.set_alignment("left")

        separator(p)

        # ----------------------------------------------------------------
        # Paper motion
        # ----------------------------------------------------------------
        p.set_print_mode(bold=True, underline=True)
        p.print_line("2. PAPER MOTION")
        p.set_print_mode()

        p.print_text("Feed 2 mm: [")
        p.feed_mm(2)
        p.print_line("]  <- 2 mm gap")
        p.feed(1)
        separator(p)

        # ----------------------------------------------------------------
        # Barcodes
        # ----------------------------------------------------------------
        p.set_print_mode(bold=True, underline=True)
        p.print_line("3. BARCODES")
        p.set_print_mode()

        p.set_alignment("center")
        p.print_line("Code 128")
        p.barcode(BARCODE_CODE128, "TCX6145DEMO", height=60, hri=2)
        p.feed(1)

        p.print_line("EAN-13")
        p.barcode(BARCODE_EAN13, "5901234123457", height=60, hri=2)
        p.feed(1)

        p.print_line("Code 39")
        p.barcode(BARCODE_CODE39, "CODE39-AB", height=80, hri=2)
        p.feed(1)

        p.set_alignment("left")
        separator(p)

        # ----------------------------------------------------------------
        # QR codes
        # ----------------------------------------------------------------
        p.set_print_mode(bold=True, underline=True)
        p.print_line("4. QR CODES")
        p.set_print_mode()

        p.set_alignment("center")
        p.print_line("URL (auto scale)")
        p.qr_code("https://github.com/toshiba", ec=QR_EC_M)
        p.feed(1)

        p.print_line("Plain text (EC=H)")
        p.qr_code("Toshiba TCx 6145-1TN\nIBM SureMark USB driver", ec=QR_EC_H)
        p.feed(1)

        p.set_alignment("left")
        separator(p)

        # ----------------------------------------------------------------
        # Raster image
        # ----------------------------------------------------------------
        p.set_print_mode(bold=True, underline=True)
        p.print_line("5. RASTER IMAGE")
        p.set_print_mode()

        p.set_alignment("center")
        print("  Generating test image …")
        img = make_test_image(width=400, height=120)
        p.print_line("Generated pattern (dithered):")
        p.print_image(img, max_width=400, dither=True)
        p.feed(1)

        p.print_line("Same image (hard threshold):")
        p.print_image(img, max_width=400, dither=False, threshold=128)
        p.feed(1)

        p.set_alignment("left")
        separator(p)

        # ----------------------------------------------------------------
        # Cash drawer
        # ----------------------------------------------------------------
        p.set_print_mode(bold=True, underline=True)
        p.print_line("6. CASH DRAWER (DK port)")
        p.set_print_mode()

        p.print_line("Sending open pulse ...")
        p.open_cash_drawer(drawer=0, on_ms=50, off_ms=50)
        p.print_line("Pulse sent (50ms on/off)")

        separator(p)

        # ----------------------------------------------------------------
        # Footer
        # ----------------------------------------------------------------
        p.set_alignment("center")
        p.set_print_mode(bold=True, double_wide=True)
        p.print_line("ALL TESTS DONE")
        p.set_print_mode()
        p.print_line("Toshiba 6145 Python Driver")
        p.print_line("Protocol: IBM SureMark USB")
        p.print_line("(reverse-engineered via usbmon)")

        p.feed(4)
        p.cut()

        print("Done — check your receipt.")

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
    finally:
        p.disconnect()


if __name__ == "__main__":
    main()
