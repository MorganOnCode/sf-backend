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
import hashlib
import re

MAX_PHOTO_BYTES = 512 * 1024
"""
Largest decoded image accepted.

Deliberately modest. A photo is only ever shown as an avatar, clients downscale
before uploading, and the ceiling has to leave room for the whole `data:` URL to
fit inside a Next.js server action's 1 MB default request body.
"""

MAX_PHOTO_LABEL = f"{MAX_PHOTO_BYTES // 1024} KB"
"""How the limit is described to a client."""

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
        raise ValueError(f"photo must be {MAX_PHOTO_LABEL} or smaller")

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
        raise ValueError(f"photo must be {MAX_PHOTO_LABEL} or smaller")

    if not signature(raw):
        raise ValueError(f"photo does not contain a valid {media_type} image")

    return photo


def decode_photo(photo: str) -> tuple[str, bytes]:
    """
    Split a validated photo into its media type and raw bytes.

    Only ever called with a value that already passed `validate_photo` on the way
    in, so a mismatch here means the stored data was corrupted, not that a client
    sent something bad.
    """
    match = _DATA_URL.fullmatch(photo)
    if match is None:
        raise ValueError("stored photo is not a base64 data URL")
    return match.group("media_type"), base64.b64decode(match.group("data"), validate=True)


def photo_etag(photo: str) -> str:
    """A strong ETag for a photo, so an unchanged avatar is re-fetched as a 304."""
    return f'"{hashlib.sha256(photo.encode()).hexdigest()[:32]}"'


# A quoted entity tag, optionally marked weak. Tags are *scanned* out of the
# header rather than split on commas, because a tag is allowed to contain one.
_ENTITY_TAG = re.compile(r'(?:W/)?"[^"]*"')


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """
    Whether an `If-None-Match` header says the client already has this photo.

    Per RFC 9110 §13.1.2 the header is a *list*, `*` stands for any current
    representation, and revalidating a `GET` compares tags *weakly* — `W/"x"`
    matches `"x"`, since the two differ only in ways a cache does not care
    about. Testing the raw header against our one tag instead would answer
    `200` to every well-formed request that is not a single verbatim echo,
    re-sending an image the client already holds.
    """
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    return any(
        (tag[2:] if tag.startswith("W/") else tag) == etag
        for tag in _ENTITY_TAG.findall(if_none_match)
    )
