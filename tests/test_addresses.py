"""
A contact has many addresses.

The address used to be five columns on `contacts`, which allowed exactly one.
It is now its own table with a foreign key back to the contact and a `type`,
so a person can have a home address and a work address at once.
"""

import pytest
from sqlalchemy import text

from app.database import engine
from app.schemas import MAX_ADDRESSES

BASE = "/api/v1/contacts"

HOME = {
    "type": "home",
    "street": "12 St James's Square",
    "city": "London",
    "postal_code": "SW1Y 4JH",
    "country": "UK",
}
WORK = {"type": "work", "street": "1 Dorset Street", "city": "London", "country": "UK"}
OTHER = {"type": "other", "street": "Ockham Park", "city": "Surrey", "country": "UK"}


def create(client, payload, **overrides) -> dict:
    return client.post(BASE, json={**payload, **overrides}).json()


# ── the relationship ────────────────────────────────────────────────────────


def test_a_contact_can_hold_several_addresses(client, payload):
    contact = create(client, payload, addresses=[HOME, WORK, OTHER])

    assert [address["type"] for address in contact["addresses"]] == ["home", "work", "other"]
    assert contact["addresses"][0]["street"] == HOME["street"]


def test_each_address_gets_its_own_id(client, payload):
    contact = create(client, payload, addresses=[HOME, WORK])

    ids = [address["id"] for address in contact["addresses"]]
    assert len(set(ids)) == 2
    assert all(isinstance(address_id, int) for address_id in ids)


def test_addresses_are_rows_pointing_back_at_their_contact(client, payload):
    """The link is a real foreign key column, not a blob on the contact."""
    contact = create(client, payload, addresses=[HOME, WORK])

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT contact_id, type FROM addresses ORDER BY id")
        ).all()

    assert [row.contact_id for row in rows] == [contact["id"], contact["id"]]
    assert [row.type for row in rows] == ["home", "work"]


def test_the_contact_table_no_longer_carries_an_address(client):
    """The flat columns are gone — they were what limited a contact to one."""
    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(contacts)"))}

    assert not columns & {"address", "city", "state", "postal_code", "country"}


def test_a_contact_with_no_addresses_is_fine(client, payload):
    assert create(client, payload, addresses=[])["addresses"] == []


def test_addresses_come_back_in_a_stable_order(client, payload):
    contact_id = create(client, payload, addresses=[OTHER, HOME, WORK])["id"]

    first = client.get(f"{BASE}/{contact_id}").json()["addresses"]
    second = client.get(f"{BASE}/{contact_id}").json()["addresses"]

    assert [a["id"] for a in first] == [a["id"] for a in second]
    assert [a["type"] for a in first] == ["other", "home", "work"]


# ── deletion cascades ───────────────────────────────────────────────────────


def test_deleting_a_contact_deletes_its_addresses(client, payload):
    contact_id = create(client, payload, addresses=[HOME, WORK])["id"]

    assert client.delete(f"{BASE}/{contact_id}").status_code == 204

    with engine.connect() as connection:
        remaining = connection.execute(text("SELECT COUNT(*) FROM addresses")).scalar_one()
    assert remaining == 0


def test_one_contacts_addresses_survive_anothers_deletion(client, payload):
    keeper = create(client, payload, addresses=[HOME])
    doomed = create(client, payload, email="grace@example.com", addresses=[WORK])

    client.delete(f"{BASE}/{doomed['id']}")

    assert len(client.get(f"{BASE}/{keeper['id']}").json()["addresses"]) == 1


# ── replacement semantics ───────────────────────────────────────────────────


def test_put_replaces_the_whole_set(client, payload):
    contact_id = create(client, payload, addresses=[HOME, WORK])["id"]

    replaced = client.put(
        f"{BASE}/{contact_id}",
        json={**payload, "addresses": [OTHER]},
    ).json()

    assert [address["type"] for address in replaced["addresses"]] == ["other"]


def test_put_without_addresses_clears_them(client, payload):
    """`PUT` is a full replacement, so an omitted list means "no addresses"."""
    contact_id = create(client, payload, addresses=[HOME, WORK])["id"]

    replaced = client.put(
        f"{BASE}/{contact_id}",
        json={"first_name": "Ada", "last_name": "Lovelace", "email": payload["email"]},
    ).json()

    assert replaced["addresses"] == []


def test_replacing_addresses_leaves_no_orphan_rows(client, payload):
    contact_id = create(client, payload, addresses=[HOME, WORK])["id"]

    client.put(f"{BASE}/{contact_id}", json={**payload, "addresses": [OTHER]})

    with engine.connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM addresses")).scalar_one()
    assert count == 1


def test_patch_leaves_addresses_alone_when_the_key_is_absent(client, payload):
    contact_id = create(client, payload, addresses=[HOME, WORK])["id"]

    patched = client.patch(f"{BASE}/{contact_id}", json={"company": "Analytical Engines"}).json()

    assert len(patched["addresses"]) == 2


def test_patch_with_an_empty_list_clears_them(client, payload):
    contact_id = create(client, payload, addresses=[HOME, WORK])["id"]

    assert client.patch(f"{BASE}/{contact_id}", json={"addresses": []}).json()["addresses"] == []


# ── validation ──────────────────────────────────────────────────────────────


def test_type_defaults_to_home(client, payload):
    contact = create(client, payload, addresses=[{"street": "Somewhere"}])
    assert contact["addresses"][0]["type"] == "home"


@pytest.mark.parametrize("bad_type", ["HOME", "office", "", None, 3])
def test_an_unknown_type_is_rejected(client, payload, bad_type):
    response = client.post(BASE, json={**payload, "addresses": [{**HOME, "type": bad_type}]})
    assert response.status_code == 422


def test_too_many_addresses_are_rejected(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [HOME] * (MAX_ADDRESSES + 1)})
    assert response.status_code == 422


def test_an_over_long_street_is_rejected(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [{**HOME, "street": "x" * 301}]})
    assert response.status_code == 422


# ── list pages ──────────────────────────────────────────────────────────────


def test_list_items_do_not_carry_addresses(client, payload):
    """Same reasoning as the photo: a list page stays small."""
    create(client, payload, addresses=[HOME, WORK])

    item = client.get(BASE).json()["items"][0]

    assert "addresses" not in item
