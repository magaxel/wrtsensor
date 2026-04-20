#!/usr/bin/env python3
"""Redact IPv4/IPv6 addresses in screenshots.

Runs tesseract OCR on each image, locates any address-like tokens (including
joined-on-the-same-line runs that tesseract split on punctuation), and paints
a background-coloured rectangle over them with a generic replacement string
drawn on top. The goal is to produce doc-ready screenshots without leaking
real LAN addresses.

Usage:
    python3 tools/redact_ips.py <image> [image ...] [--suffix _redacted]
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IPV6_RE = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}\b")

IPV4_REPLACEMENT = "192.0.2.1"
IPV6_REPLACEMENT = "2001:db8::1"

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


@dataclass
class Word:
    text: str
    x: int
    y: int
    w: int
    h: int
    line_key: tuple[int, int, int, int]


def ocr_words(image_path: Path) -> list[Word]:
    result = subprocess.run(
        ["tesseract", str(image_path), "stdout", "-c", "tessedit_create_tsv=1", "--psm", "6"],
        capture_output=True,
        text=True,
        check=True,
    )
    words: list[Word] = []
    reader = csv.DictReader(io.StringIO(result.stdout), delimiter="\t")
    for row in reader:
        text = row.get("text", "").strip()
        if not text:
            continue
        try:
            words.append(
                Word(
                    text=text,
                    x=int(row["left"]),
                    y=int(row["top"]),
                    w=int(row["width"]),
                    h=int(row["height"]),
                    line_key=(
                        int(row["page_num"]),
                        int(row["block_num"]),
                        int(row["par_num"]),
                        int(row["line_num"]),
                    ),
                )
            )
        except (KeyError, ValueError):
            continue
    return words


def find_ip_boxes(words: list[Word]) -> list[tuple[int, int, int, int, str]]:
    """Reassemble each OCR line, locate IP substrings, and map back to pixel boxes."""
    by_line: dict[tuple, list[Word]] = {}
    for w in words:
        by_line.setdefault(w.line_key, []).append(w)

    boxes: list[tuple[int, int, int, int, str]] = []
    for line_words in by_line.values():
        line_words.sort(key=lambda w: w.x)
        joined = ""
        spans: list[tuple[int, int, Word]] = []  # (start_in_joined, end_in_joined, word)
        for i, w in enumerate(line_words):
            if i > 0:
                joined += " "
            start = len(joined)
            joined += w.text
            spans.append((start, len(joined), w))

        for pattern, replacement in ((IPV4_RE, IPV4_REPLACEMENT), (IPV6_RE, IPV6_REPLACEMENT)):
            for m in pattern.finditer(joined):
                start, end = m.span()
                covered = [w for (s, e, w) in spans if e > start and s < end]
                if not covered:
                    continue
                x0 = min(w.x for w in covered)
                y0 = min(w.y for w in covered)
                x1 = max(w.x + w.w for w in covered)
                y1 = max(w.y + w.h for w in covered)
                boxes.append((x0, y0, x1, y1, replacement))
    return boxes


def pick_font(px_height: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    target = max(8, int(px_height * 0.85))
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, target)
    return ImageFont.load_default()


def sample_bg(image: Image.Image, x: int, y: int, h: int) -> tuple[int, int, int]:
    """Sample a pixel just to the left of the redaction box to match card background."""
    px = max(0, x - 4)
    py = min(image.height - 1, y + h // 2)
    pixel = image.getpixel((px, py))
    if isinstance(pixel, int):
        return (pixel, pixel, pixel)
    return pixel[:3]


def redact(image_path: Path, out_path: Path) -> int:
    words = ocr_words(image_path)
    boxes = find_ip_boxes(words)
    if not boxes:
        return 0

    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    for x0, y0, x1, y1, replacement in boxes:
        bg = sample_bg(img, x0, y0, y1 - y0)
        draw.rectangle([x0 - 2, y0 - 2, x1 + 2, y1 + 2], fill=bg)
        font = pick_font(y1 - y0)
        text_bbox = draw.textbbox((0, 0), replacement, font=font)
        tw = text_bbox[2] - text_bbox[0]
        th = text_bbox[3] - text_bbox[1]
        tx = x0 + max(0, ((x1 - x0) - tw) // 2)
        ty = y0 + max(0, ((y1 - y0) - th) // 2) - text_bbox[1]
        # Pick a readable foreground (invert the background luminance).
        lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
        fg = (220, 220, 220) if lum < 128 else (40, 40, 40)
        draw.text((tx, ty), replacement, fill=fg, font=font)

    img.save(out_path)
    return len(boxes)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("--suffix", default="_redacted")
    args = ap.parse_args()

    for src in args.images:
        if not src.exists():
            print(f"skip (missing): {src}", file=sys.stderr)
            continue
        dst = src.with_stem(src.stem + args.suffix)
        count = redact(src, dst)
        print(f"{src}: {count} address(es) redacted → {dst}")


if __name__ == "__main__":
    main()
