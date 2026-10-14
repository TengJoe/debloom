#!/usr/bin/python3
"""Render a product introduction from verified screenshots and the canonical SVG."""

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cairo
import gi

gi.require_version("Rsvg", "2.0")
from gi.repository import Rsvg

from install import render_icons

ROOT = Path(__file__).resolve().parent.parent


def uri(path):
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def main():
    assets = ROOT / "docs/assets"
    (assets / "logo.png").write_bytes(render_icons()[512])
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000" viewBox="0 0 1600 1000">
      <defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#07101f"/><stop offset="1" stop-color="#14254a"/></linearGradient></defs>
      <rect width="1600" height="1000" fill="url(#bg)"/>
      <image href="{uri(assets / "logo.png")}" x="60" y="56" width="100" height="100"/>
      <g font-family="Noto Sans CJK SC, sans-serif">
        <text x="184" y="108" font-size="54" font-weight="700" fill="#f8fafc">Debloom</text>
        <text x="186" y="150" font-size="22" fill="#cbd5e1">A calmer way to install local DEBs.</text>
        <text x="70" y="236" font-size="22" fill="#bfdbfe">中文 · 浅色</text>
        <text x="834" y="236" font-size="22" fill="#bfdbfe">English · Dark</text>
        <rect x="60" y="256" width="720" height="580" rx="18" fill="#273958"/>
        <rect x="820" y="256" width="720" height="580" rx="18" fill="#273958"/>
        <image href="{uri(assets / "zh-light.png")}" x="70" y="270" width="700" height="538.46"/>
        <image href="{uri(assets / "en-dark.png")}" x="830" y="270" width="700" height="538.46"/>
        <text x="70" y="906" font-size="24" font-weight="600" fill="#f8fafc">Choose → Review → Authorize</text>
        <text x="70" y="950" font-size="20" fill="#cbd5e1">Native desktop · APT preflight · 中文 / English · Light / Dark / System</text>
      </g>
    </svg>'''
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1600, 1000)
    handle = Rsvg.Handle.new_from_data(svg.encode())
    rect = Rsvg.Rectangle()
    rect.x = rect.y = 0
    rect.width = 1600
    rect.height = 1000
    assert handle.render_document(cairo.Context(surface), rect)
    surface.write_to_png(str(assets / "cover.png"))
    print("Rendered cover from real application screenshots.")


if __name__ == "__main__":
    main()
