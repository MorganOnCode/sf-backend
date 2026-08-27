"""
vCard export.

The format's sharp edges are escaping, folding and line endings, so those are
tested against the rendered text rather than through a parser — a parser would
paper over exactly the mistakes worth catching.
"""

import base64

import pytest

from app.vcard import MAX_LINE_OCTETS, render_vcard

BASE = "/api/v1/contacts"

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"pixels" * 40).decode()
HOME = {"type": "home", "street": "12 St James's Square", "city": "London", "country": "UK"}
WORK = {"type": "work", "street": "1 Market St", "city": "San Francisco", "state": "CA"}


@pytest.fixture
def card(client, payload) -> str:
    """One contact with a photo and two typed addresses, rendered."""
    contact_id = client.post(
        BASE, json={**payload, "photo": PNG, "addresses": [HOME, WORK]}
    ).json()["id"]
    return client.get(f"{BASE}/{contact_id}/vcard").text


def unfold(document: str) -> list[str]:
    """Undo the folding, so a test can assert on whole logical lines."""
    return document.replace("\r\n ", "").rstrip("\r\n").split("\r\n")


# ── document shape ──────────────────────────────────────────────────────────


def test_it_is_a_vcard_4_document(card):
    lines = unfold(card)
    assert lines[0] == "BEGIN:VCARD"
    assert lines[1] == "VERSION:4.0"
    assert lines[-1] == "END:VCARD"


def test_every_line_ends_with_crlf(card):
    assert card.endswith("\r\n")
    assert "\n" not in card.replace("\r\n", "")


def test_the_name_is_both_display_and_structured(card):
    lines = unfold(card)
    assert "FN:Ada Lovelace" in lines
    assert "N:Lovelace;Ada;;;" in lines


def test_the_contact_details_come_through(card):
    lines = unfold(card)
    assert "EMAIL;TYPE=work:ada@example.com" in lines
    assert "ORG:Analytical Engines" in lines
    assert "TITLE:Mathematician" in lines


def test_a_stable_uid_lets_a_reimport_update_rather_than_duplicate(card):
    assert any(line.startswith("UID:sf-contacts:") for line in unfold(card))


# ── the point of the feature ────────────────────────────────────────────────


def test_each_address_is_its_own_line_carrying_its_type(card):
    addresses = [line for line in unfold(card) if line.startswith("ADR")]

    assert len(addresses) == 2
    assert addresses[0] == "ADR;TYPE=home:;;12 St James's Square;London;;;UK"
    assert addresses[1] == "ADR;TYPE=work:;;1 Market St;San Francisco;CA;;"


def test_a_contact_with_no_addresses_has_no_adr_lines(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": []}).json()["id"]

    card = client.get(f"{BASE}/{contact_id}/vcard").text

    assert not [line for line in unfold(card) if line.startswith("ADR")]


def test_the_photo_travels_with_the_card(card):
    photo = next(line for line in unfold(card) if line.startswith("PHOTO:"))
    assert photo == f"PHOTO:{PNG}"


def test_a_contact_with_no_photo_has_no_photo_line(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]

    card = client.get(f"{BASE}/{contact_id}/vcard").text

    assert not [line for line in unfold(card) if line.startswith("PHOTO")]


# ── escaping and folding ────────────────────────────────────────────────────


def test_special_characters_are_escaped(client, payload):
    contact_id = client.post(
        BASE,
        json={**payload, "company": "Babbage; Lovelace, Ltd\\Co", "notes": "line one\nline two"},
    ).json()["id"]

    lines = unfold(client.get(f"{BASE}/{contact_id}/vcard").text)

    assert r"ORG:Babbage\; Lovelace\, Ltd\\Co" in lines
    assert "NOTE:line one\\nline two" in lines


def test_no_line_exceeds_the_octet_limit(card):
    for line in card.rstrip("\r\n").split("\r\n"):
        assert len(line.encode("utf-8")) <= MAX_LINE_OCTETS, line[:40]


def test_folded_lines_are_marked_as_continuations(card):
    physical = card.rstrip("\r\n").split("\r\n")
    assert any(line.startswith(" ") for line in physical), "expected the photo to fold"


def test_unfolding_gives_the_value_back_exactly(card):
    photo = next(line for line in unfold(card) if line.startswith("PHOTO:"))
    assert photo.removeprefix("PHOTO:") == PNG


def test_folding_never_splits_a_character(client, payload):
    """The limit is counted in octets, but a multi-byte character is indivisible."""
    contact_id = client.post(BASE, json={**payload, "notes": "é" * 120}).json()["id"]

    card = client.get(f"{BASE}/{contact_id}/vcard").text

    # A split character would make this raise rather than compare unequal.
    assert "é" * 120 in card.replace("\r\n ", "")


# ── endpoints ───────────────────────────────────────────────────────────────


def test_a_single_card_downloads_under_the_person_s_name(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]

    response = client.get(f"{BASE}/{contact_id}/vcard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/vcard")
    assert response.headers["content-disposition"] == 'attachment; filename="ada-lovelace.vcf"'


def test_an_unknown_contact_is_a_404(client):
    assert client.get(f"{BASE}/9999/vcard").status_code == 404


def test_the_whole_book_exports_as_one_document(client, payload):
    client.post(BASE, json=payload)
    client.post(BASE, json={**payload, "email": "grace@example.com", "last_name": "Hopper"})

    response = client.get(f"{BASE}/vcard")

    assert response.text.count("BEGIN:VCARD") == 2
    assert response.headers["content-disposition"] == 'attachment; filename="contacts.vcf"'


def test_the_export_honours_the_same_search_as_the_list(client, payload):
    client.post(BASE, json=payload)
    client.post(BASE, json={**payload, "email": "grace@example.com", "last_name": "Hopper"})

    document = client.get(f"{BASE}/vcard", params={"search": "hopper"}).text

    assert document.count("BEGIN:VCARD") == 1
    assert "FN:Ada Hopper" in unfold(document)


def test_the_export_is_bounded(client, payload):
    for index in range(4):
        client.post(BASE, json={**payload, "email": f"user{index}@example.com"})

    document = client.get(f"{BASE}/vcard", params={"limit": 2}).text

    assert document.count("BEGIN:VCARD") == 2


def test_an_empty_book_renders_an_empty_document(client):
    assert client.get(f"{BASE}/vcard").text == ""


def test_render_vcard_handles_no_contacts():
    assert render_vcard([]) == ""
