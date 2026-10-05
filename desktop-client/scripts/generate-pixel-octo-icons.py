"""Create Windows icons from the web app's pixel octopus favicon.

Requires Pillow: python -m pip install Pillow
"""

from pathlib import Path
from io import BytesIO
from struct import pack
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "frontend/public"
OUTPUT = ROOT / "desktop-client/public/logo/pixel-octo"
OUTPUT.mkdir(parents=True, exist_ok=True)

icon = Image.open(SOURCE / "favicon.png").convert("RGBA")
for size in (64, 128, 512):
    icon.resize((size, size), Image.Resampling.NEAREST).save(
        OUTPUT / f"{size}x{size}.png"
    )

Image.open(SOURCE / "favicon-16.png").convert("RGBA").save(OUTPUT / "16x16.png")

frames = []
for size in (16, 32, 48, 64, 128, 256):
    if size in (16, 32):
        frame = Image.open(SOURCE / f"favicon-{size}.png").convert("RGBA")
    else:
        frame = icon.resize((size, size), Image.Resampling.NEAREST)
    buffer = BytesIO()
    frame.save(buffer, format="PNG")
    frames.append((size, buffer.getvalue()))

offset = 6 + 16 * len(frames)
with (OUTPUT / "icon.ico").open("wb") as file:
    file.write(pack("<HHH", 0, 1, len(frames)))
    for size, data in frames:
        file.write(pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    for _, data in frames:
        file.write(data)
