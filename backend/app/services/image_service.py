from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.config import get_settings

log = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 64_000_000

MIME_TYPES: dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}

SUPPORTED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"})

_GIF_FRAME = 0


class ImageError(RuntimeError):
    """Raised for user-correctable image problems (surfaced as 400)."""


@dataclass(frozen=True)
class PreparedImage:
    mime_type: str
    data: bytes
    width: int
    height: int
    original_bytes: int
    downscaled: bool

    def as_inline_part(self) -> dict[str, object]:
        """The shape Gemini's `contents[].parts[]` expects for inline bytes."""
        return {
            "inline_data": {
                "mime_type": self.mime_type,
                "data": base64.b64encode(self.data).decode("ascii"),
            }
        }


def _open(path: Path) -> Image.Image:
    try:
        image = Image.open(path)
        image.load() 
        return image
    except UnidentifiedImageError as exc:
        raise ImageError(
            "This file is not a readable image. The content does not match its extension."
        ) from exc
    except (OSError, ValueError) as exc:
        raise ImageError(f"Could not read this image: {exc}") from exc


def _fit_edge(image: Image.Image, max_edge: int) -> tuple[Image.Image, bool]:
    width, height = image.size
    longest = max(width, height)
    if longest <= max_edge:
        return image, False
    scale = max_edge / float(longest)
    new_width = max(1, round(width * scale))
    new_height = max(1, round(height * scale))
    return image.resize((new_width, new_height), Image.LANCZOS), True


def _flatten(image: Image.Image) -> Image.Image:
    """Composite transparency onto white.

    Gemini reads alpha as "transparent", and a dark logo on a transparent
    background becomes a dark blob once flattened to black. White matches
    what the user actually sees.
    """
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def prepare_image(path: Path) -> PreparedImage:
    """Validate, downscale and re-encode an uploaded image for the model."""
    if not path.exists():
        raise ImageError("Uploaded file is missing.")

    settings = get_settings()
    original_bytes = path.stat().st_size
    image = _open(path)

    fmt = (image.format or "").upper()
    if fmt not in MIME_TYPES:
        raise ImageError(
            f"{fmt or 'This'} images are not supported. "
            f"Accepted: PNG, JPEG, WebP, GIF, BMP, TIFF."
        )

    try:
        if fmt == "GIF":
            image.seek(_GIF_FRAME)
        image = _flatten(image)
        image, downscaled = _fit_edge(image, settings.max_image_edge_px)
        width, height = image.size

        buffer = io.BytesIO()
        encode_as_jpeg = original_bytes > settings.image_jpeg_threshold_bytes
        if encode_as_jpeg:
            image.save(buffer, format="JPEG", quality=settings.image_jpeg_quality, optimize=True)
            mime = MIME_TYPES["JPEG"]
        else:
            image.save(buffer, format="PNG", optimize=True)
            mime = MIME_TYPES[fmt]

        data = buffer.getvalue()
    except (OSError, ValueError) as exc:
        raise ImageError(f"Could not process this image: {exc}") from exc
    finally:
        image.close()

    if not data:
        raise ImageError("This image produced no readable data.")

    log.info(
        "Prepared image %s: %s->%s bytes, %dx%d, mime=%s, downscaled=%s",
        path.name, original_bytes, len(data), width, height, mime, downscaled,
    )
    return PreparedImage(
        mime_type=mime,
        data=data,
        width=width,
        height=height,
        original_bytes=original_bytes,
        downscaled=downscaled,
    )
