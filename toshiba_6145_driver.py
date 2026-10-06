#!/usr/bin/env python3
"""
Toshiba TCx 6145 Receipt Printer & Cash Drawer Driver
======================================================
Reverse-engineered from the Toshiba VSP (Virtual Serial Port) Linux driver
v1.2601 via usbmon USB traffic capture.

Supports:
  - Toshiba TCx 6145-1TN receipt printer (USB VID 0x0F66 PID 0x4535)
  - Cash drawer connected to printer DK/RJ11 port (via ESC p command)
  - USB standalone cash drawer (VID 0x0F66 PID 0x455A / 0x4559, VID 0x04B3 PID 0x4557)

See README.md for full protocol documentation.
"""

import math
import struct
import time
import usb.core
import usb.util

# ---------------------------------------------------------------------------
# Printer USB constants
# ---------------------------------------------------------------------------
PRINTER_VENDOR_ID  = 0x0F66
PRINTER_PRODUCT_ID = 0x4535

PRINTER_INTERFACE = 2   # Interface 2 = USB Printer class (Bulk OUT/IN)
EP_BULK_OUT       = 0x03
EP_BULK_IN        = 0x83

MAX_PAYLOAD = 251   # length byte = len(payload)+4 must fit in 0xFF → max payload 251

# ---------------------------------------------------------------------------
# USB Cash Drawer device IDs
# ---------------------------------------------------------------------------
CD_DEVICES = [
    (0x0F66, 0x455A),   # Toshiba TCx Cash Drawer (newer)
    (0x0F66, 0x4559),   # Toshiba TCx Cash Drawer (alternate)
    (0x04B3, 0x4557),   # IBM 4694 Cash Drawer
]
CD_INTERFACE = 0        # HID interface on the standalone cash drawer
CD_EP_IN     = 0x81    # Interrupt IN  (status)
CD_EP_OUT    = 0x01    # Interrupt OUT (commands)

# ---------------------------------------------------------------------------
# IBM SureMark USB framing
# ---------------------------------------------------------------------------

def _wrap(payload: bytes) -> bytes:
    """Prepend the 7-byte IBM SureMark USB header to a payload.

    Discovered via usbmon capture of the Toshiba VSP Linux driver.
    Every bulk-OUT transfer uses this exact framing:

        Byte 0  : 0x01  (constant – "print data" command type)
        Byte 1  : len(payload) + 4
        Byte 2  : 0x00  (constant)
        Byte 3  : 0x01  (constant)
        Bytes 4-6: 0x00 0x00 0x00  (constant)
        Bytes 7+: payload (raw ESC/POS bytes)
    """
    length = len(payload) + 4
    if length > 0xFF:
        raise ValueError(f"Payload too large for single wrap: {len(payload)} bytes")
    return bytes([0x01, length, 0x00, 0x01, 0x00, 0x00, 0x00]) + payload


# ---------------------------------------------------------------------------
# Printer init sequence (replayed from usbmon capture at VSP startup)
# Each of these command payloads is individually wrapped and sent before
# any print data.
# ---------------------------------------------------------------------------
_INIT_SEQUENCE = [
    bytes([0x10, 0x05, 0x60]),  # DLE ENQ 0x60 – real-time status probe
    bytes([0x00, 0x40, 0x00]),  # null padding (ignored by printer firmware)
    bytes([0x10, 0x05, 0x64]),  # DLE ENQ 0x64 – real-time status probe
    bytes([0x1D, 0x49, 0x01]),  # GS | SOH     – request printer ID type 1
    bytes([0x10, 0x05, 0x63]),  # DLE ENQ 0x63 – real-time status probe
    bytes([0x00, 0x40, 0x00]),  # null padding
    bytes([0x1B, 0x74, 0x13]),  # ESC t 0x13   – set code page CP1252
]

# ---------------------------------------------------------------------------
# ESC/POS command constants
# ---------------------------------------------------------------------------
ESC = 0x1B
GS  = 0x1D
DLE = 0x10
ENQ = 0x05

def _esc(*args): return bytes([ESC] + list(args))
def _gs(*args):  return bytes([GS]  + list(args))

# Initialise / reset
CMD_INIT              = _esc(0x40)          # ESC @

# Text alignment  (ESC a n)
CMD_ALIGN_LEFT        = _esc(0x61, 0x00)
CMD_ALIGN_CENTER      = _esc(0x61, 0x01)
CMD_ALIGN_RIGHT       = _esc(0x61, 0x02)

# Bold  (ESC E n / ESC G n)
CMD_BOLD_ON           = _esc(0x45, 0x01)    # ESC E 1
CMD_BOLD_OFF          = _esc(0x45, 0x00)

# Underline  (ESC - n)
CMD_UNDERLINE_ON      = _esc(0x2D, 0x01)
CMD_UNDERLINE_OFF     = _esc(0x2D, 0x00)

# Overline  (ESC _ n / ESC 0x5F n)
CMD_OVERLINE_ON       = _esc(0x5F, 0x01)
CMD_OVERLINE_OFF      = _esc(0x5F, 0x00)

# Invert (white on black)  (ESC H n)
CMD_INVERT_ON         = _esc(0x48, 0x01)
CMD_INVERT_OFF        = _esc(0x48, 0x00)

# Double-wide / double-high  (ESC W n, ESC h n)
CMD_DOUBLE_WIDE_ON    = _esc(0x57, 0x01)
CMD_DOUBLE_WIDE_OFF   = _esc(0x57, 0x00)
CMD_DOUBLE_HIGH_ON    = _esc(0x68, 0x01)
CMD_DOUBLE_HIGH_OFF   = _esc(0x68, 0x00)

# Paper feed
CMD_LF                = bytes([0x0A])
CMD_FF_CUT            = bytes([0x0C])       # print + feed + cut

# Paper cuts  (GS V n)
CMD_CUT_FULL          = _gs(0x56, 0x00)
CMD_CUT_PARTIAL       = _gs(0x56, 0x01)

# Feed n lines  (ESC d n)
def cmd_feed_lines(n: int) -> bytes:
    return _esc(0x64, n & 0xFF)

# Feed n dot-steps  (ESC J n, 204 steps/inch in receipt station)
def cmd_feed_steps(n: int) -> bytes:
    return _esc(0x4A, n & 0xFF)

# Code page  (ESC t n) — see CODEPAGES dict below
def cmd_codepage(n: int) -> bytes:
    return _esc(0x74, n & 0xFF)

CODEPAGES = {
    437:  0x00, 858:  0x01, 863:  0x02, 860:  0x03,
    865:  0x04, 869:  0x07, 857:  0x08, 864:  0x09,
    852:  0x0B, 866:  0x0D, 1250: 0x11, 1251: 0x12,
    1252: 0x13, 1253: 0x14, 1254: 0x15, 1255: 0x16,
    1256: 0x17, 1257: 0x18,
}

# Print mode bitmap  (ESC ! n)
PRINT_MODE_BOLD        = 0x08
PRINT_MODE_DOUBLE_HIGH = 0x10
PRINT_MODE_DOUBLE_WIDE = 0x20
PRINT_MODE_UNDERLINE   = 0x80
def cmd_print_mode(flags: int) -> bytes:
    return _esc(0x21, flags & 0xFF)

# Barcode settings
def cmd_barcode_width(n: int) -> bytes:   return _gs(0x77, n)        # GS w n (2-4)
def cmd_barcode_height(n: int) -> bytes:  return _gs(0x68, n)        # GS h n (1-255)
def cmd_barcode_hri(pos: int) -> bytes:   return _gs(0x48, pos)      # GS H n (0=none,1=above,2=below,3=both)

# Barcode print
# n=0x00..0x06: Epson ESC/POS format-1, null-terminated  → GS k n data 00
# n=0x49 (73):  Epson ESC/POS format-2, length-prefixed  → GS k 49 len {B data
# Epson ESC/POS barcode types
# Format-1 (n=0x00-0x06): GS k n data NUL  — null-terminated, older subset
# Format-2 (n=0x41-0x49): GS k n len data  — length-prefixed, full subset
BARCODE_UPCA    = 0x41  # format-2: length-prefixed
BARCODE_UPCE    = 0x42
BARCODE_EAN13   = 0x02  # format-1: null-terminated (confirmed working)
BARCODE_EAN8    = 0x44
BARCODE_CODE39  = 0x45  # format-2: length-prefixed
BARCODE_ITF     = 0x46
BARCODE_CODABAR = 0x47
BARCODE_CODE93  = 0x48
BARCODE_CODE128 = 0x49  # format-2 + {B subset prefix (confirmed working)

# set of format-2 (length-prefixed) symbologies
_BARCODE_FMT2 = {BARCODE_UPCA, BARCODE_UPCE, BARCODE_EAN8,
                 BARCODE_CODE39, BARCODE_ITF, BARCODE_CODABAR,
                 BARCODE_CODE93, BARCODE_CODE128}

def cmd_barcode(symbology: int, data: str) -> bytes:
    """Build a GS k barcode command.

    Code 128  (0x49): GS k 49 len {B data   (format-2, {B = subset B)
    Code 39   (0x45): format-1 null-term for len ≤ 9, format-2 len-prefix for len ≥ 11.
                      len=10 is unsupported: firmware max for format-1 is 9,
                      and format-2 length byte 0x0A is treated as LF.
    EAN-13    (0x02): GS k 02 data NUL       (format-1, null-terminated)
    Others (fmt-2):   GS k n len data
    """
    payload = data.encode("ascii")
    if symbology == BARCODE_CODE128:
        inner = b"{B" + payload
        return _gs(0x6B, BARCODE_CODE128) + bytes([len(inner)]) + inner
    if symbology == BARCODE_CODE39:
        n = len(payload)
        if n == 10:
            raise ValueError(
                "Code 39 length=10 is unsupported on this printer: "
                "format-1 max is 9 chars; format-2 length byte 0x0A is "
                "interpreted as LF. Use 1-9 or 11+ characters."
            )
        if n <= 9:
            # format-1: null-terminated, n=0x04
            return _gs(0x6B, 0x04) + payload + b"\x00"
        # format-2: length-prefixed, n=0x45
        return _gs(0x6B, BARCODE_CODE39) + bytes([n]) + payload
    if symbology in _BARCODE_FMT2:
        return _gs(0x6B, symbology) + bytes([len(payload)]) + payload
    # format-1 null-terminated (EAN-13 = 0x02)
    return _gs(0x6B, symbology) + payload + b"\x00"

# QR code — generated as a raster bitmap via the Python qrcode library.
# The printer's native QR command (GS O) is an IBM-native instruction and is
# not recognised in Epson ESC/POS emulation mode, so we generate the QR matrix
# ourselves and print it with the same GS v 0 raster path as print_image().
QR_EC_L = "L"
QR_EC_M = "M"
QR_EC_Q = "Q"
QR_EC_H = "H"

# Cash drawer pulse via printer DK port  (ESC p m n1 n2)
def cmd_cash_drawer(drawer: int = 0, on_ms: int = 50, off_ms: int = 50) -> bytes:
    """Open a cash drawer wired to the printer's DK/RJ11 port.

    drawer  : 0 = drawer 1 (pin 2), 1 = drawer 2 (pin 5)
    on_ms   : pulse-on  duration in milliseconds (stored as ms // 2)
    off_ms  : pulse-off duration in milliseconds (stored as ms // 2)
    """
    return _esc(0x70, drawer & 0x01, max(1, on_ms // 2), max(1, off_ms // 2))

# Real-time control  (DLE ENQ n)
CMD_REALTIME_RELEASE = bytes([DLE, ENQ, 0x31])   # release held print buffer
CMD_REALTIME_CANCEL  = bytes([DLE, ENQ, 0x32])   # cancel print buffer
CMD_REALTIME_STATUS  = bytes([DLE, ENQ, 0x34])   # request status immediately
CMD_RESET            = bytes([DLE, ENQ, 0x40])   # hard reset
CMD_UNSOLICITED_ON   = bytes([DLE, ENQ, 0x41])   # enable unsolicited status
CMD_UNSOLICITED_OFF  = bytes([DLE, ENQ, 0x42])   # disable unsolicited status

# Prepare for power-off  (ESC g)
CMD_PREPARE_SHUTDOWN = _esc(0x67)

# Raster image (GS v 0 m xL xH yL yH data...)
# m=0: normal density (203 dpi), m=1: double-wide, m=2: double-high, m=3: quadruple
def cmd_raster_image(bitmap_rows: list[bytes]) -> bytes:
    """Build a GS v 0 raster-image command from a list of packed row bytes."""
    x_bytes = len(bitmap_rows[0]) if bitmap_rows else 0
    y_dots  = len(bitmap_rows)
    header = bytes([
        GS, 0x76, 0x30, 0x00,
        x_bytes & 0xFF, (x_bytes >> 8) & 0xFF,
        y_dots  & 0xFF, (y_dots  >> 8) & 0xFF,
    ])
    return header + b"".join(bitmap_rows)


# ===========================================================================
# Printer class
# ===========================================================================

class Toshiba6145:
    """Driver for the Toshiba TCx 6145 receipt printer over USB.

    Usage::

        with Toshiba6145() as p:
            p.print_receipt([
                {"text": "Hello!", "align": "center", "bold": True},
                "Normal line",
            ])

    Or imperative::

        p = Toshiba6145()
        p.connect()
        p.set_alignment("center")
        p.set_bold(True)
        p.print_line("Big Title")
        p.set_bold(False)
        p.set_alignment("left")
        p.feed(3)
        p.cut()
        p.disconnect()
    """

    def __init__(self, serial_number: str = None):
        self.serial_number = serial_number
        self.dev = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self):
        """Find the printer on USB, claim Interface 2, and send init sequence."""
        kwargs = dict(idVendor=PRINTER_VENDOR_ID, idProduct=PRINTER_PRODUCT_ID)
        if self.serial_number:
            kwargs["serial_number"] = self.serial_number
        self.dev = usb.core.find(**kwargs)
        if self.dev is None:
            raise RuntimeError(
                f"Toshiba 6145 not found "
                f"(VID={PRINTER_VENDOR_ID:#06x} PID={PRINTER_PRODUCT_ID:#06x})"
            )
        for iface in range(3):
            if self.dev.is_kernel_driver_active(iface):
                self.dev.detach_kernel_driver(iface)
        self.dev.set_configuration()
        usb.util.claim_interface(self.dev, PRINTER_INTERFACE)
        for cmd in _INIT_SEQUENCE:
            self._write(cmd)
            time.sleep(0.05)
        time.sleep(0.2)
        self._write(CMD_INIT)

    def disconnect(self):
        """Release the USB interface cleanly."""
        if self.dev is not None:
            try:
                usb.util.release_interface(self.dev, PRINTER_INTERFACE)
            except Exception:
                pass
            try:
                usb.util.dispose_resources(self.dev)
            except Exception:
                pass
            self.dev = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()

    # ------------------------------------------------------------------
    # Low-level write
    # ------------------------------------------------------------------

    def _write(self, data: bytes, timeout_ms: int = 2000):
        """Wrap data in the IBM SureMark USB header and send via Bulk OUT."""
        if not data:
            return
        for i in range(0, len(data), MAX_PAYLOAD):
            chunk = data[i : i + MAX_PAYLOAD]
            self.dev.write(EP_BULK_OUT, _wrap(chunk), timeout=timeout_ms)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> int:
        """USB Printer class GET_PORT_STATUS → 1-byte status.

        Bit 3 (0x08) = NOT ERROR  (1 = no error)
        Bit 4 (0x10) = SELECT     (1 = online / selected)
        Bit 5 (0x20) = PAPER EMPTY (1 = out of paper)
        """
        result = self.dev.ctrl_transfer(
            bmRequestType=0xA1, bRequest=0x01,
            wValue=0, wIndex=PRINTER_INTERFACE, data_or_wLength=1,
        )
        return result[0] if result else 0

    def is_online(self) -> bool:
        s = self.get_status()
        return bool(s & 0x10) and bool(s & 0x08) and not bool(s & 0x20)

    def is_paper_empty(self) -> bool:
        return bool(self.get_status() & 0x20)

    # ------------------------------------------------------------------
    # Text output
    # ------------------------------------------------------------------

    def write(self, data: bytes):
        """Send raw bytes (any ESC/POS or IBM SureMark command)."""
        self._write(data)

    def print_text(self, text: str, encoding: str = "cp1252"):
        self._write(text.encode(encoding, errors="replace"))

    def print_line(self, text: str = "", encoding: str = "cp1252"):
        self._write((text + "\n").encode(encoding, errors="replace"))

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def set_alignment(self, align: str):
        """'left', 'center', or 'right'."""
        self._write({"left": CMD_ALIGN_LEFT, "center": CMD_ALIGN_CENTER,
                     "right": CMD_ALIGN_RIGHT}[align.lower()])

    def set_bold(self, on: bool):
        self._write(CMD_BOLD_ON if on else CMD_BOLD_OFF)

    def set_underline(self, on: bool):
        self._write(CMD_UNDERLINE_ON if on else CMD_UNDERLINE_OFF)

    def set_overline(self, on: bool):
        self._write(CMD_OVERLINE_ON if on else CMD_OVERLINE_OFF)

    def set_invert(self, on: bool):
        """Print white text on black background."""
        self._write(CMD_INVERT_ON if on else CMD_INVERT_OFF)

    def set_double_wide(self, on: bool):
        self._write(CMD_DOUBLE_WIDE_ON if on else CMD_DOUBLE_WIDE_OFF)

    def set_double_high(self, on: bool):
        self._write(CMD_DOUBLE_HIGH_ON if on else CMD_DOUBLE_HIGH_OFF)

    def set_print_mode(self, bold=False, double_high=False, double_wide=False, underline=False):
        """Set multiple text attributes in one command."""
        flags = 0
        if bold:        flags |= PRINT_MODE_BOLD
        if double_high: flags |= PRINT_MODE_DOUBLE_HIGH
        if double_wide: flags |= PRINT_MODE_DOUBLE_WIDE
        if underline:   flags |= PRINT_MODE_UNDERLINE
        self._write(cmd_print_mode(flags))

    def set_codepage(self, codepage: int):
        """Set character code page. Common values: 437, 858, 1252."""
        n = CODEPAGES.get(codepage)
        if n is None:
            raise ValueError(f"Unsupported code page {codepage}. Known: {list(CODEPAGES)}")
        self._write(cmd_codepage(n))

    # ------------------------------------------------------------------
    # Paper motion
    # ------------------------------------------------------------------

    def feed(self, lines: int = 3):
        """Feed n blank lines."""
        self._write(cmd_feed_lines(lines))

    def feed_mm(self, mm: float):
        """Feed a precise distance in millimetres (204 steps/inch, 8 steps/mm)."""
        steps = int(mm * 8)
        # ESC J takes values 0-255 (≈ 32 mm); chain if longer
        while steps > 0:
            self._write(cmd_feed_steps(min(steps, 255)))
            steps -= 255

    def cut(self, partial: bool = False):
        self._write(CMD_CUT_PARTIAL if partial else CMD_CUT_FULL)

    # ------------------------------------------------------------------
    # Barcodes
    # ------------------------------------------------------------------

    def barcode(self, symbology: int, data: str,
                height: int = 80, width: int = 3, hri: int = 2):
        """Print a barcode.

        symbology : BARCODE_* constant (e.g. BARCODE_CODE128)
        height    : bar height in dots (1-255). Minimum ~80 for Code 39;
                    values below ~64 are silently ignored by this printer.
        width     : bar width multiplier (2-4)
        hri       : human-readable position (0=none, 1=above, 2=below, 3=both)
        """
        self._write(cmd_barcode_height(height))
        self._write(cmd_barcode_width(width))
        self._write(cmd_barcode_hri(hri))
        self._write(cmd_barcode(symbology, data))

    def qr_code(self, data: str, ec: str = QR_EC_M, module_size: int = 4):
        """Print a QR code as a raster bitmap.

        Uses the Python 'qrcode' library to generate the QR matrix and prints
        it via GS v 0 raster (same path as print_image). The printer's native
        GS O QR command is IBM-mode only and not recognised in ESC/POS mode.

        data        : string to encode
        ec          : error correction  QR_EC_L / QR_EC_M / QR_EC_Q / QR_EC_H
        module_size : pixels per QR module (3-6 recommended for 203 dpi)
        """
        import qrcode
        from PIL import Image

        qr = qrcode.QRCode(
            error_correction={
                "L": qrcode.constants.ERROR_CORRECT_L,
                "M": qrcode.constants.ERROR_CORRECT_M,
                "Q": qrcode.constants.ERROR_CORRECT_Q,
                "H": qrcode.constants.ERROR_CORRECT_H,
            }[ec],
            box_size=module_size,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        self.print_image(img, dither=False, threshold=128)

    def print_image(self, image, max_width: int = 576, threshold: int = 128,
                    dither: bool = True):
        """Print a PIL Image (or a path to one) as a raster bitmap.

        The printer is 203 dpi, 80 mm paper, ~576 dots wide printable.
        Images wider than max_width are scaled down proportionally.

        image     : PIL.Image.Image instance, or str/Path file path
        max_width : maximum dot width (default 576 = full 80 mm roll)
        threshold : luminance cutoff when dither=False (0-255)
        dither    : use Floyd-Steinberg dithering (True) or hard threshold (False)
        """
        from PIL import Image

        if not isinstance(image, Image.Image):
            image = Image.open(image)

        image = image.convert("RGBA")

        # White background for transparent images
        bg = Image.new("RGBA", image.size, (255, 255, 255, 255))
        bg.paste(image, mask=image.split()[3])
        image = bg.convert("RGB")

        # Scale to fit roll width
        w, h = image.size
        if w > max_width:
            h = int(h * max_width / w)
            w = max_width
            image = image.resize((w, h), Image.LANCZOS)

        # Pad width to a multiple of 8
        w_pad = math.ceil(w / 8) * 8
        if w_pad != w:
            padded = Image.new("RGB", (w_pad, h), (255, 255, 255))
            padded.paste(image, (0, 0))
            image = padded
            w = w_pad

        # Convert to 1-bit
        if dither:
            mono = image.convert("1")
        else:
            gray = image.convert("L")
            mono = gray.point(lambda p: 0 if p < threshold else 255, "1")

        # Pack rows into bytes (PIL "1" stores 1 bit per pixel, row-padded to bytes)
        x_bytes = w // 8
        rows = []
        for y in range(h):
            row = bytearray(x_bytes)
            for bx in range(x_bytes):
                byte = 0
                for bit in range(8):
                    px = mono.getpixel((bx * 8 + bit, y))
                    if px == 0:     # black pixel → set bit
                        byte |= (0x80 >> bit)
                row[bx] = byte
            rows.append(bytes(row))

        cmd = cmd_raster_image(rows)
        self._write(cmd)

    # ------------------------------------------------------------------
    # Cash drawer (connected to printer DK / RJ11 port)
    # ------------------------------------------------------------------

    def open_cash_drawer(self, drawer: int = 0, on_ms: int = 50, off_ms: int = 50):
        """Send a drive pulse to open a cash drawer wired to the printer.

        drawer : 0 = first drawer (pin 2), 1 = second drawer (pin 5)
        on_ms  : pulse-on  width in ms (printer uses 2 ms units; min effective ~10 ms)
        off_ms : pulse-off width in ms
        """
        self._write(cmd_cash_drawer(drawer, on_ms, off_ms))

    # ------------------------------------------------------------------
    # High-level receipt helper
    # ------------------------------------------------------------------

    def print_receipt(self, lines: list, encoding: str = "cp1252"):
        """Print a formatted receipt and cut.

        Each item in `lines` is either:
          - a str  → printed left-aligned
          - a dict → supported keys:
              text        (str)
              align       'left' | 'center' | 'right'
              bold        bool
              underline   bool
              double_wide bool
              double_high bool
              invert      bool
        """
        self._write(CMD_INIT)
        time.sleep(0.3)

        for item in lines:
            if isinstance(item, str):
                self.print_line(item, encoding)
                continue

            # apply attributes
            if item.get("bold"):        self._write(CMD_BOLD_ON)
            if item.get("underline"):   self._write(CMD_UNDERLINE_ON)
            if item.get("double_wide"): self._write(CMD_DOUBLE_WIDE_ON)
            if item.get("double_high"): self._write(CMD_DOUBLE_HIGH_ON)
            if item.get("invert"):      self._write(CMD_INVERT_ON)
            if "align" in item:
                self.set_alignment(item["align"])

            self.print_line(item.get("text", ""), encoding)

            # reset attributes
            self._write(CMD_BOLD_OFF)
            self._write(CMD_ALIGN_LEFT)

        self.feed(3)
        self.cut()


# ===========================================================================
# USB Cash Drawer class (standalone USB device, separate from printer)
# ===========================================================================

class ToshibaUsbCashDrawer:
    """Driver for standalone Toshiba / IBM USB cash drawers.

    Supported hardware (VID:PID):
      0x0F66:0x455A  Toshiba TCx Cash Drawer
      0x0F66:0x4559  Toshiba TCx Cash Drawer (alternate firmware)
      0x04B3:0x4557  IBM 4694 Cash Drawer

    Protocol notes (reverse-engineered from VSP binary analysis):
      - Device is USB HID, uses interrupt endpoints
      - Commands are sent via interrupt OUT (EP 0x01)
      - Status is read via interrupt IN  (EP 0x81)
      - Status response is 2 bytes: [status_byte_1, status_byte_2]
      - Status byte bits (from VSP guide):
          bit 6 = Drawer 1 Open
          bit 5 = Drawer 2 Open
          bit 4 = Drawer 1 Present
          bit 3 = Drawer 2 Present
          bit 2 = Unsolicited status enabled

    The command format passed over the interrupt OUT endpoint mirrors the
    VSP RS-232 command set:
      0x07        Open Drawer 1
      0x1B 0x07   Open Drawer 2
      0x06        Read Status
      0x1B 0x06   Toggle unsolicited status
    """

    STATUS_DRAWER1_OPEN    = 0x40
    STATUS_DRAWER2_OPEN    = 0x20
    STATUS_DRAWER1_PRESENT = 0x10
    STATUS_DRAWER2_PRESENT = 0x08
    STATUS_UNSOLICITED_ON  = 0x04

    def __init__(self, vid: int = None, pid: int = None):
        self.vid = vid
        self.pid = pid
        self.dev = None

    def connect(self):
        """Locate and claim the USB cash drawer."""
        if self.vid and self.pid:
            self.dev = usb.core.find(idVendor=self.vid, idProduct=self.pid)
        else:
            for vid, pid in CD_DEVICES:
                self.dev = usb.core.find(idVendor=vid, idProduct=pid)
                if self.dev:
                    break
        if self.dev is None:
            raise RuntimeError("No supported USB cash drawer found")
        if self.dev.is_kernel_driver_active(CD_INTERFACE):
            self.dev.detach_kernel_driver(CD_INTERFACE)
        self.dev.set_configuration()
        usb.util.claim_interface(self.dev, CD_INTERFACE)

    def disconnect(self):
        if self.dev is not None:
            try:
                usb.util.release_interface(self.dev, CD_INTERFACE)
            except Exception:
                pass
            try:
                usb.util.dispose_resources(self.dev)
            except Exception:
                pass
            self.dev = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()

    def _send(self, data: bytes, timeout_ms: int = 1000):
        self.dev.write(CD_EP_OUT, data, timeout=timeout_ms)

    def _recv(self, size: int = 8, timeout_ms: int = 1000) -> bytes:
        try:
            return bytes(self.dev.read(CD_EP_IN, size, timeout=timeout_ms))
        except usb.core.USBTimeoutError:
            return b""

    def open_drawer1(self):
        """Send pulse to open cash drawer 1."""
        self._send(bytes([0x07]))

    def open_drawer2(self):
        """Send pulse to open cash drawer 2."""
        self._send(bytes([0x1B, 0x07]))

    def read_status(self) -> dict:
        """Request and return cash drawer status.

        Returns a dict with keys:
          drawer1_open, drawer2_open, drawer1_present, drawer2_present
        """
        self._send(bytes([0x06]))
        raw = self._recv()
        status = raw[0] if raw else 0
        return {
            "drawer1_open":    bool(status & self.STATUS_DRAWER1_OPEN),
            "drawer2_open":    bool(status & self.STATUS_DRAWER2_OPEN),
            "drawer1_present": bool(status & self.STATUS_DRAWER1_PRESENT),
            "drawer2_present": bool(status & self.STATUS_DRAWER2_PRESENT),
            "raw":             status,
        }

    def enable_unsolicited_status(self, enable: bool = True):
        """Toggle unsolicited status reporting (device sends status on change)."""
        self._send(bytes([0x1B, 0x06]))


# ===========================================================================
# Command-line test
# ===========================================================================

if __name__ == "__main__":
    import sys

    print("=" * 50)
    print("Toshiba 6145 Python Driver — Self-Test")
    print("=" * 50)

    with Toshiba6145() as p:
        status = p.get_status()
        print(f"Printer status: {status:#04x}  online={p.is_online()}  paper_empty={p.is_paper_empty()}")

        p.print_receipt([
            {"text": "TOSHIBA TCx 6145-1TN", "align": "center", "bold": True, "double_wide": True},
            "",
            {"text": "Protocol: IBM SureMark USB", "align": "center"},
            {"text": "Driver:   Pure Python + libusb", "align": "center"},
            {"text": "Firmware: 08.01", "align": "center"},
            "",
            {"text": "=" * 32, "align": "center"},
            "",
            {"text": "QR Code Demo", "align": "center"},
        ])

        p.set_alignment("center")
        p.qr_code("https://github.com", ec=QR_EC_M)
        p.feed(2)

        p.set_alignment("center")
        p.barcode(BARCODE_CODE128, "123456789", height=60, hri=2)
        p.feed(2)

        p.print_receipt([
            {"text": "Cash drawer test:", "bold": True},
            "Sending open pulse ...",
        ])
        p.open_cash_drawer(drawer=0, on_ms=50, off_ms=50)

        p.print_receipt([
            {"text": "=== SELF-TEST COMPLETE ===", "align": "center", "bold": True},
        ])

    print("Done.")
