# Toshiba TCx 6145-1TN USB Printer — Reverse-Engineering Notes

# Disclaimer

Yes, this is AI-assisted, this is a one-night project to get this printer running without Toshiba's software with plain ESC/POS.
The code itself is as vibey as it gets but it works for my use case. Maybe this is usable as a POC for an actual driver.

**Legal:** Toshiba, TCx, and SureMark are trademarks of Toshiba Global Commerce Solutions. This project is not affiliated with, endorsed by, or supported by Toshiba in any way. The Toshiba name is referenced solely for identification of the hardware this driver was written for.

**Liability:** This software is provided "as is", without warranty of any kind. Use at your own risk. If your device breaks, catches fire, opens your cash drawer at 3 am, or does anything else unexpected — that is not my problem.

If you are from Toshiba and have concerns, feel free to reach out: toshiba@l-lutz.de

## Hardware

| Property | Value |
|---|---|
| Model | Toshiba TCx 6145-1TN |
| Mechanism | Direct thermal, 80 mm |
| Resolution | 203 dpi |
| USB VID:PID | `0x0F66:0x4535` |
| Firmware | 08.01 |
| Serial | 41-DB202 |
| Interfaces | 0: HID (firmware), 1: HID (status), 2: USB Printer class |
| Bulk OUT | EP `0x03`, 512-byte packets |
| Bulk IN | EP `0x83`, 512-byte packets |
| Cash drawer port | DK / RJ11 on back |

---

## The Problem: The Printer Silently Ignores Everything

When you first plug in this printer and send ESC/POS commands via the USB Printer class interface (Interface 2, Bulk OUT), **nothing happens**. No error, no paper movement, no response. Not even the printer's internal self-test produces output if you try to trigger it with software commands.

The same is true for raw IBM 4610 native commands. The printer acknowledges USB transfers (no USB-level error), but prints nothing.

This is because every byte you write to this printer must be wrapped in a **7-byte IBM SureMark USB framing header**. Without this header the printer's firmware silently discards every packet.

---

## Discovery: The Toshiba VSP Driver

Toshiba ships a proprietary Linux binary called `vsd` (Virtual Serial Port Daemon), distributed as part of the [VSP Linux package](https://tgcs04.toshibacommerce.com/cs/groups/internet/documents/document/dnnw/x2xp/~edisp/vsp_linux.zip).

`vsd` creates a pseudo-terminal (`/dev/ttyS10` by default), accepts serial data from applications, and bridges it to the USB printer. With `Emulation_Mode=2` (Epson/ESC/POS) set in `VSDConfig.xml`, `vsd` acts as a pure pass-through — it wraps whatever arrives on the PTY in the USB framing and forwards it unchanged.

Capturing that bridge with `usbmon` revealed the framing protocol.

### Setting up usbmon capture

```bash
modprobe usbmon
# find your printer bus number first:
lsusb  # note Bus 001 Device 007 for 0f66:4535

# capture to file (as root):
cat /sys/kernel/debug/usb/usbmon/1t > /tmp/cap.txt &
# ... run vsd and send a test print ...
kill %1
grep "^.*Bo:.*" /tmp/cap.txt | head -20
```

### What the capture showed

Every USB Bulk OUT packet sent by `vsd` begins with the same 7 bytes before the payload:

```
Offset  Value  Meaning
0       0x01   Command type: "print data"
1       N+4    Length = len(payload) + 4
2       0x00   Constant
3       0x01   Constant
4       0x00   Constant
5       0x00   Constant
6       0x00   Constant
7+      ...    Raw ESC/POS payload
```

Maximum payload per packet is **505 bytes** (keeping the total ≤ 512).

---

## The IBM SureMark USB Protocol

```python
def wrap(payload: bytes) -> bytes:
    """Prepend IBM SureMark USB framing to an ESC/POS payload."""
    length = len(payload) + 4
    return bytes([0x01, length, 0x00, 0x01, 0x00, 0x00, 0x00]) + payload
```

This is the entire protocol. Every `libusb_bulk_transfer` to EP `0x03` must use this wrapper around the real data. Large payloads are split into ≤ 505-byte chunks, each individually wrapped.

---

## Printer Initialisation Sequence

When `vsd` starts, it sends 7 specific command packets before any print data. These are replayed exactly from the usbmon capture:

| # | Bytes (hex) | Meaning |
|---|---|---|
| 1 | `10 05 60` | `DLE ENQ 0x60` — real-time status probe |
| 2 | `00 40 00` | Null padding (ignored by firmware) |
| 3 | `10 05 64` | `DLE ENQ 0x64` — real-time status probe |
| 4 | `1D 49 01` | `GS I 0x01` — request printer ID type 1 |
| 5 | `10 05 63` | `DLE ENQ 0x63` — real-time status probe |
| 6 | `00 40 00` | Null padding |
| 7 | `1B 74 13` | `ESC t 0x13` — select code page CP1252 |

After a short delay, `ESC @` (printer reset) is sent to clear any partial state.

The printer will not accept print commands reliably without this sequence being sent first at connection time.

---

## ESC/POS Command Reference

All commands below are sent as the payload inside the USB wrapper.

### Text Formatting

| Command | Bytes | Effect |
|---|---|---|
| `ESC @` | `1B 40` | Initialize / reset |
| `ESC a n` | `1B 61 n` | Alignment: 0=left, 1=center, 2=right |
| `ESC E n` | `1B 45 n` | Bold: 1=on, 0=off |
| `ESC - n` | `1B 2D n` | Underline: 1=on, 0=off |
| `ESC W n` | `1B 57 n` | Double-wide: 1=on, 0=off |
| `ESC h n` | `1B 68 n` | Double-high: 1=on, 0=off |
| `ESC H n` | `1B 48 n` | Invert (white-on-black): 1=on, 0=off |
| `ESC ! n` | `1B 21 n` | Print mode bitmap (see below) |

**`ESC ! n` bitmap:**

| Bit | Value | Effect |
|---|---|---|
| 3 | 0x08 | Bold |
| 4 | 0x10 | Double-high |
| 5 | 0x20 | Double-wide |
| 7 | 0x80 | Underline |

### Paper Motion

| Command | Bytes | Effect |
|---|---|---|
| `LF` | `0A` | Feed one line |
| `ESC d n` | `1B 64 n` | Feed n lines |
| `ESC J n` | `1B 4A n` | Feed n dot-rows (204 rows/inch) |
| `GS V 0` | `1D 56 00` | Full cut |
| `GS V 1` | `1D 56 01` | Partial cut |

### Cash Drawer (DK / RJ11 port on printer)

```
ESC p  m  n1 n2
1B 70  m  n1 n2
```

| Parameter | Meaning |
|---|---|
| `m` | Drawer: 0 = drawer 1 (pin 2), 1 = drawer 2 (pin 5) |
| `n1` | Pulse-on  width × 2 ms (e.g. 0x19 = 50 ms) |
| `n2` | Pulse-off width × 2 ms |

Typical call: `1B 70 00 19 19` — open drawer 1, 50 ms on/off.

The drawer must be wired to the printer's DK port. The signal goes high for `n1×2 ms` then stays low for `n2×2 ms`.

### Barcodes

```
GS k  n  data  00
1D 6B n  data  00
```

Common symbologies:

| `n` | Symbology |
|---|---|
| 0x00 | UPC-A |
| 0x01 | UPC-E |
| 0x02 | EAN-13 |
| 0x03 | EAN-8 |
| 0x04 | Code 39 |
| 0x05 | ITF |
| 0x06 | Codabar |
| 0x09 | Code 128 (auto A/B/C) |

Barcode size:
```
GS h n    1D 68 n    Bar height in dots (1-255, default 80)
GS w n    1D 77 n    Bar width multiplier (2-4)
GS H n    1D 48 n    HRI position: 0=none, 1=above, 2=below, 3=both
```

### QR Codes

```
GS O  n1 n2 n3  data  00
1D 4F n1 n2 n3  data  00
```

| Param | Value | Meaning |
|---|---|---|
| n1 | 0x05 | Auto encoding mode |
| n2 | 0x00 | Error correction L (~7%) |
| n2 | 0x01 | Error correction M (~15%) |
| n2 | 0x02 | Error correction Q (~25%) |
| n2 | 0x03 | Error correction H (~30%) |
| n3 | 0x00 | (reserved, always 0) |

Scale: `GS _ s` = `1D 5F s` — `s=0xFF` = auto (recommended, produces ~3 cm QR)

### Code Page

```
ESC t n    1B 74 n
```

Key values: 0x00=CP437, 0x13=CP1252, 0x14=CP1253, 0x17=CP1256. CP1252 is the default the init sequence sets.

---

## USB Printer Class Status

The standard USB Printer class `GET_PORT_STATUS` control transfer works:

```python
result = dev.ctrl_transfer(
    bmRequestType=0xA1, bRequest=0x01,
    wValue=0, wIndex=2, data_or_wLength=1,
)
status = result[0]
```

| Bit | Mask | Meaning |
|---|---|---|
| 3 | 0x08 | NOT ERROR (1 = no error) |
| 4 | 0x10 | SELECT    (1 = online/selected) |
| 5 | 0x20 | PAPER EMPTY (1 = paper out) |

Nominal online value: `0x18` (bits 3 and 4 set, bit 5 clear).

---

## Real-Time Commands (DLE ENQ)

These can be sent at any time, even during printing:

| Bytes | Effect |
|---|---|
| `10 05 31` | Release held print buffer |
| `10 05 32` | Cancel print buffer |
| `10 05 34` | Request immediate status |
| `10 05 40` | Hard reset |
| `10 05 41` | Enable unsolicited status |
| `10 05 42` | Disable unsolicited status |

---

## Standalone USB Cash Drawer

A separate USB cash drawer (not wired to the printer) presents as USB HID and uses **interrupt** transfers, not bulk. Known supported PIDs:

| VID:PID | Device |
|---|---|
| `0x0F66:0x455A` | Toshiba TCx Cash Drawer |
| `0x0F66:0x4559` | Toshiba TCx Cash Drawer (alt.) |
| `0x04B3:0x4557` | IBM 4694 Cash Drawer |

These devices use a command set derived from the VSP RS-232 virtual port:

| Byte(s) | Action |
|---|---|
| `07` | Open drawer 1 |
| `1B 07` | Open drawer 2 |
| `06` | Read status |
| `1B 06` | Toggle unsolicited status |

Status byte bit layout (2-byte response, first byte):

| Bit | Mask | Meaning |
|---|---|---|
| 6 | 0x40 | Drawer 1 open |
| 5 | 0x20 | Drawer 2 open |
| 4 | 0x10 | Drawer 1 present/connected |
| 3 | 0x08 | Drawer 2 present/connected |
| 2 | 0x04 | Unsolicited status enabled |

---

## Python Driver Usage

The `toshiba_6145_driver.py` in this directory is a pure-Python replacement for the Toshiba `vsd` binary. It requires only `pyusb` (which uses `libusb`).

### Installation

```bash
# NixOS
nix-shell -p python3 python3Packages.pyusb

# or pip
pip install pyusb
```

### Basic usage

```python
from toshiba_6145_driver import Toshiba6145, BARCODE_CODE128, QR_EC_M

with Toshiba6145() as p:
    p.print_receipt([
        {"text": "My Shop", "align": "center", "bold": True, "double_wide": True},
        "",
        "Item 1              5.00",
        "Item 2              3.50",
        "=" * 28,
        {"text": "Total               8.50", "bold": True},
    ])
```

### Open cash drawer (DK port)

```python
with Toshiba6145() as p:
    p.open_cash_drawer(drawer=0, on_ms=50, off_ms=50)
```

### QR code + barcode

```python
with Toshiba6145() as p:
    p.set_alignment("center")
    p.qr_code("https://example.com", ec=QR_EC_M)
    p.feed(2)
    p.barcode(BARCODE_CODE128, "123456789", height=60, hri=2)
    p.feed(3)
    p.cut()
```

### USB cash drawer (separate device)

```python
from toshiba_6145_driver import ToshibaUsbCashDrawer

with ToshibaUsbCashDrawer() as cd:
    status = cd.read_status()
    print(status)       # {'drawer1_open': False, 'drawer1_present': True, ...}
    cd.open_drawer1()
```

---

## Running and Spying on the Official Toshiba VSP Driver

This section documents how to run the official `vsd` binary and intercept its
USB traffic with `usbmon`. This is how the IBM SureMark framing protocol was
reverse-engineered.

### Getting the VSP package

Download from Toshiba Commerce (login may be required):
```
https://tgcs04.toshibacommerce.com/cs/groups/internet/documents/document/dnnw/x2xp/~edisp/vsp_linux.zip
```

Extract the `.deb` inside:
```bash
unzip vsp_linux.zip -d vsp_linux
cd vsp_linux
ar x toshiba-vsp-linux_*.deb
tar xf data.tar.xz -C /tmp/vsp_data
```

### Patching the binary for NixOS

`vsd` is a pre-built glibc binary. On NixOS the dynamic linker lives in the
Nix store, so patchelf is required:

```bash
nix-shell -p patchelf
GLIBC=$(ls /nix/store | grep "glibc-[0-9]" | grep -v "dev\|doc\|man\|bin\|static\|debug" | head -1)
patchelf --set-interpreter "/nix/store/${GLIBC}/lib/ld-linux-x86-64.so.2" \
         --set-rpath       "/nix/store/${GLIBC}/lib" \
         /opt/tgcs/vsp/bin/vsd
```

### Configuring VSDConfig.xml

Set the printer's VID, PID and serial number, and choose Epson emulation
(mode 2) so `vsd` passes ESC/POS through unchanged:

```xml
<PRINTER_USB PID="4535" Serial_Number="41-DB202" VID="0f66">/dev/ttyS10</PRINTER_USB>
<Printer_Configuration>
    <Emulation_Mode>2</Emulation_Mode>
    <Unsolicited_Status>0</Unsolicited_Status>
</Printer_Configuration>
```

Enable verbose logging in `VSDLogging.xml`:
```xml
<Log_Level>5</Log_Level>
```

### Blacklisting usblp

The kernel `usblp` module claims the printer before libusb can. Blacklist it:

```bash
echo "blacklist usblp" > /etc/modprobe.d/no-usblp.conf
rmmod usblp   # unload immediately without rebooting
```

On NixOS, add to `configuration.nix` instead:
```nix
boot.blacklistedKernelModules = [ "usblp" ];
```

### Running vsd

```bash
# Run as root; vsd daemonises itself (output goes to /dev/null by default)
/opt/tgcs/vsp/bin/vsd -c /opt/tgcs/vsp/VSDConfig.xml

# After ~1 second /dev/ttyS10 (a PTY symlink) appears
ls -la /dev/ttyS10

# Send a test print
printf '\x1b\x40Hello from serial\n\x1d\x56\x00' > /dev/ttyS10
```

### Spying on USB traffic with usbmon

`usbmon` is a kernel subsystem that exposes raw USB packets as a text stream.
Run it while `vsd` is bridging data and you can read every byte sent to the
printer.

```bash
# Load the module
modprobe usbmon

# Find which bus the printer is on
nix-shell -p usbutils --run "lsusb"
# e.g. "Bus 001 Device 007: ID 0f66:4535" → bus 1

# Start capturing in the background
cat /sys/kernel/debug/usb/usbmon/1t > /tmp/usbmon.txt &

# Trigger a print via vsd
printf '\x1b\x40Test\n\x1d\x56\x00' > /dev/ttyS10
sleep 1

# Stop capture
kill %1

# Show only Bulk OUT packets to the printer (device 7 on bus 1)
grep "Bo:1:007" /tmp/usbmon.txt
```

### Reading the usbmon output

Each line looks like:
```
ffff... 123456 S Bo:1:007:2 -115 10 = 01070001 00000010 056043...
                              ↑    ↑    ↑
                              |    |    first bytes of the packet
                              |    length
                              S=submit C=complete
```

The first 7 bytes of every Bulk OUT payload are the IBM SureMark header:
```
01 [len+4] 00 01 00 00 00  [ESC/POS payload follows]
```

Filter only the submit lines and strip the header to see the raw ESC/POS:
```bash
grep " S Bo:1:007" /tmp/usbmon.txt | awk '{print $NF}' | \
  sed 's/.\{14\}//'   # remove the 7-byte header (14 hex chars)
```

### The Python driver replaces all of this

The Python driver talks directly to USB Interface 2 via libusb. No `vsd`,
no PTY, no serial port emulation needed — just:

```python
from toshiba_6145_driver import Toshiba6145
with Toshiba6145() as p:
    p.print_line("Hello")
    p.cut()
```

---

## Files

| File | Purpose |
|---|---|
| `toshiba_6145_driver.py` | Pure Python printer + cash drawer driver |
| `VSDConfig.xml` | VSP daemon configuration (Epson emulation, printer serial) |
| `VSDLogging.xml` | VSP daemon logging (level 5 = verbose) |
| `bin/vsd` | Toshiba official VSP binary (patched with patchelf for NixOS) |

---

## References

- IBM 4610 TM7/TF7 Programming Guide (p/n GA27-4601) — authoritative ESC/POS command reference for this printer family
- Toshiba VSP Linux User Guide — documents the virtual serial port protocol and cash drawer RS-232 commands
- [USB Printer class spec](https://www.usb.org/sites/default/files/usbprint11.pdf) — covers GET_PORT_STATUS control transfer
- Linux `usbmon` — used to capture USB traffic from `vsd` and reverse the framing protocol
