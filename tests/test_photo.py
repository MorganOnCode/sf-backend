"""Contact photos: the `data:` URL validator and the endpoints that accept one."""

import base64

import pytest

from app.photo import MAX_PHOTO_BYTES, validate_photo

BASE = "/api/v1/contacts"

PNG_HEADER = b"\x89PNG\r\n\x1a\n"
JPEG_HEADER = b"\xff\xd8\xff"


def data_url(raw: bytes, media_type: str = "image/png") -> str:
    return f"data:{media_type};base64,{base64.b64encode(raw).decode()}"


PNG = data_url(PNG_HEADER + b"pixels")


def test_blank_photo_normalises_to_none():
    assert validate_photo(None) is None
    assert validate_photo("   ") is None


def test_valid_photo_is_returned_unchanged():
    assert validate_photo(PNG) == PNG


@pytest.mark.parametrize(
    "photo",
    [
        pytest.param("https://example.com/ada.png", id="not-a-data-url"),
        pytest.param("data:image/png,notbase64", id="missing-base64-marker"),
        pytest.param("data:image/png;base64,!!!not-base64!!!", id="not-base64"),
        pytest.param(data_url(b"<svg/>", "image/svg+xml"), id="svg-can-carry-script"),
        pytest.param(data_url(b"application", "application/pdf"), id="not-an-image-type"),
        pytest.param(data_url(b"definitely not a png"), id="magic-number-mismatch"),
        pytest.param(data_url(JPEG_HEADER + b"body", "image/png"), id="media-type-lies"),
    ],
)
def test_rejected_photos(photo):
    with pytest.raises(ValueError):
        validate_photo(photo)


def test_photo_over_the_size_limit_is_rejected():
    oversized = data_url(PNG_HEADER + b"\x00" * MAX_PHOTO_BYTES)
    with pytest.raises(ValueError, match="MB or smaller"):
        validate_photo(oversized)


def test_create_with_photo(client, payload):
    response = client.post(BASE, json={**payload, "photo": PNG})
    assert response.status_code == 201
    assert response.json()["photo"] == PNG


def test_create_without_photo_defaults_to_none(client, payload):
    assert client.post(BASE, json=payload).json()["photo"] is None


def test_create_with_invalid_photo_is_rejected(client, payload):
    response = client.post(BASE, json={**payload, "photo": "https://example.com/ada.png"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "photo"


def test_photo_is_returned_by_get_and_list(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PNG}).json()["id"]
    assert client.get(f"{BASE}/{contact_id}").json()["photo"] == PNG
    assert client.get(BASE).json()["items"][0]["photo"] == PNG


def test_patch_can_set_and_clear_the_photo(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]

    assert client.patch(f"{BASE}/{contact_id}", json={"photo": PNG}).json()["photo"] == PNG
    assert client.patch(f"{BASE}/{contact_id}", json={"photo": None}).json()["photo"] is None


def test_put_without_a_photo_clears_it(client, payload):
    """`PUT` is a full replacement, so a client that drops the field loses the photo."""
    contact_id = client.post(BASE, json={**payload, "photo": PNG}).json()["id"]

    replaced = client.put(
        f"{BASE}/{contact_id}",
        json={"first_name": "Ada", "last_name": "Lovelace", "email": payload["email"]},
    )
    assert replaced.json()["photo"] is None


def test_put_carrying_the_photo_through_keeps_it(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PNG}).json()["id"]

    replaced = client.put(
        f"{BASE}/{contact_id}",
        json={"first_name": "Ada", "last_name": "Lovelace", "email": payload["email"], "photo": PNG},
    )
    assert replaced.json()["photo"] == PNG
