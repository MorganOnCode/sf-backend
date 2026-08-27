"""
Validation for contact photos.

The service is self-contained — there is no object store to upload to — so a
photo travels and is stored as a `data:image/...;base64,...` URL on the contact
itself. That makes it attacker-controlled text that later ends up in an `<img
src>`, so it is checked three ways before it is accepted:

1. the `data:` URL shape, so nothing else can masquerade as one;
2. an allow-list of raster media types — SVG is deliberately excluded because it
   can carry script;
3. the decoded bytes' magic number, so the declared media type cannot lie.
"""

import base64
import binascii
import re

MAX_PHOTO_BYTES = 2 * 1024 * 1024
"""Largest decoded image accepted."""

# Reject an oversized string before spending memory decoding it. Base64 inflates
# by 4/3, plus room for the `data:...;base64,` prefix.
_MAX_DATA_URL_CHARS = (MAX_PHOTO_BYTES + 2) // 3 * 4 + 64

_DATA_URL = re.compile(r"data:(?P<media_type>image/[a-z0-9.+-]+);base64,(?P<data>[A-Za-z0-9+/]*={0,2})")


def _is_webp(raw: bytes) -> bool:
    # RIFF container: "RIFF" <4-byte size> "WEBP".
    return raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"


_SIGNATURES = {
    "image/jpeg": lambda raw: raw[:3] == b"\xff\xd8\xff",
    "image/png": lambda raw: raw[:8] == b"\x89PNG\r\n\x1a\n",
    "image/gif": lambda raw: raw[:6] in (b"GIF87a", b"GIF89a"),
    "image/webp": _is_webp,
}

ALLOWED_MEDIA_TYPES: tuple[str, ...] = tuple(_SIGNATURES)


def validate_photo(value: str | None) -> str | None:
    """
    Return the photo unchanged, or raise `ValueError` describing why it is not a
    usable image. Blank input normalises to `None`, i.e. "no photo".
    """
    if value is None:
        return None

    photo = value.strip()
    if not photo:
        return None

    if len(photo) > _MAX_DATA_URL_CHARS:
        raise ValueError(f"photo must be {MAX_PHOTO_BYTES // 1024 // 1024} MB or smaller")

    match = _DATA_URL.fullmatch(photo)
    if match is None:
        raise ValueError("photo must be a base64 data URL, e.g. 'data:image/png;base64,iVBORw0KGgo...'")

    media_type = match.group("media_type")
    signature = _SIGNATURES.get(media_type)
    if signature is None:
        raise ValueError(f"photo media type '{media_type}' is not supported; use one of {', '.join(ALLOWED_MEDIA_TYPES)}")

    try:
        raw = base64.b64decode(match.group("data"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("photo is not valid base64") from exc

    if len(raw) > MAX_PHOTO_BYTES:
        raise ValueError(f"photo must be {MAX_PHOTO_BYTES // 1024 // 1024} MB or smaller")

    if not signature(raw):
        raise ValueError(f"photo does not contain a valid {media_type} image")

    return photo
