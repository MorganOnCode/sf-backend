"""Contact photos: the `data:` URL validator and the endpoints that accept one."""

import base64

import pytest

from app.photo import MAX_PHOTO_BYTES, MAX_PHOTO_LABEL, validate_photo

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
    with pytest.raises(ValueError, match=f"{MAX_PHOTO_LABEL} or smaller"):
        validate_photo(oversized)


def test_the_limit_leaves_room_inside_a_1mb_request_body():
    """The whole data URL has to fit through a Next.js server action's default limit."""
    assert len(data_url(b"\x00" * MAX_PHOTO_BYTES)) < 1024 * 1024


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


def test_photo_is_returned_by_get(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PNG}).json()["id"]
    assert client.get(f"{BASE}/{contact_id}").json()["photo"] == PNG


def test_list_items_report_has_photo_instead_of_inlining_it(client, payload):
    """A page of up to 200 inline photos would be hundreds of megabytes."""
    client.post(BASE, json={**payload, "photo": PNG})
    client.post(BASE, json={**payload, "email": "grace@example.com"})

    items = client.get(BASE, params={"sort_by": "id"}).json()["items"]

    assert [item["has_photo"] for item in items] == [True, False]
    assert all("photo" not in item for item in items)


def test_the_photo_column_is_not_selected_when_listing(client, payload):
    """`has_photo` comes from SQL, so the image never leaves the database."""
    client.post(BASE, json={**payload, "photo": PNG})

    statements: list[str] = []
    from sqlalchemy import event

    from app.database import engine

    @event.listens_for(engine, "before_cursor_execute")
    def _record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    try:
        client.get(BASE)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    assert selects, "expected the list endpoint to query the database"

    # The photo is referenced only inside the `IS NOT NULL` test that produces
    # `has_photo`; it is never selected as a value, which is the point.
    columns = [statement.split("FROM")[0] for statement in selects]
    assert any("IS NOT NULL AS has_photo" in clause for clause in columns)
    assert not any("contacts.photo AS" in clause for clause in columns)


def test_photo_endpoint_serves_the_decoded_image(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PNG}).json()["id"]

    response = client.get(f"{BASE}/{contact_id}/photo")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == base64.b64decode(PNG.split(",", 1)[1])
    assert response.headers["etag"]


def test_photo_endpoint_revalidates_with_an_etag(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PNG}).json()["id"]
    etag = client.get(f"{BASE}/{contact_id}/photo").headers["etag"]

    response = client.get(f"{BASE}/{contact_id}/photo", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert not response.content


def test_photo_endpoint_404s_when_there_is_no_photo(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]

    response = client.get(f"{BASE}/{contact_id}/photo")

    assert response.status_code == 404
    assert "no photo" in response.json()["detail"]


def test_photo_endpoint_404s_for_an_unknown_contact(client):
    assert client.get(f"{BASE}/9999/photo").status_code == 404


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
