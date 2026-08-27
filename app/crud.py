from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, func, or_, select
from sqlalchemy.orm import Session

from app.models import Address, Contact
from app.schemas import AddressCreate, ContactCreate, ContactReplace, ContactUpdate

SORTABLE_FIELDS = ("id", "first_name", "last_name", "email", "company", "created_at", "updated_at")

# Every mapped column except the photo. A list page holds up to 200 rows, so the
# photo is never selected there — `has_photo` below carries the one bit a client
# needs, and the image itself is served by its own endpoint.
LIST_COLUMNS = [column for column in Contact.__table__.columns if column.name != "photo"]


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def get_contact(db: Session, contact_id: int) -> Contact | None:
    return db.get(Contact, contact_id)


def get_contact_by_email(db: Session, email: str) -> Contact | None:
    stmt = select(Contact).where(func.lower(Contact.email) == _normalize_email(email))
    return db.execute(stmt).scalar_one_or_none()


def count_contacts(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Contact)).scalar_one()


def list_contacts(
    db: Session,
    *,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "id",
    order: str = "asc",
) -> tuple[Sequence[Row[Any]], int]:
    """
    Return (page of contacts, total matching count).

    Rows carry `LIST_COLUMNS` plus `has_photo`, not whole `Contact` entities, so
    the photo column never leaves the database on a list request. They validate
    into `ContactListItem` the same way an ORM object would.
    """
    stmt = select(*LIST_COLUMNS, Contact.photo.is_not(None).label("has_photo"))

    if search:
        pattern = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Contact.first_name).like(pattern),
                func.lower(Contact.last_name).like(pattern),
                func.lower(Contact.email).like(pattern),
                func.lower(func.coalesce(Contact.company, "")).like(pattern),
                func.lower(func.coalesce(Contact.phone, "")).like(pattern),
            )
        )

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    if sort_by not in SORTABLE_FIELDS:
        sort_by = "id"
    column = getattr(Contact, sort_by)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())

    items = db.execute(stmt.limit(limit).offset(offset)).all()
    return items, total


def _build_addresses(payloads: list[AddressCreate]) -> list[Address]:
    return [Address(**payload.model_dump()) for payload in payloads]


def _set_addresses(contact: Contact, payloads: list[AddressCreate]) -> None:
    """
    Replace a contact's addresses with the ones supplied.

    Assigning the collection is enough: `delete-orphan` on the relationship
    deletes the rows that dropped out. Stored addresses are therefore replaced
    rather than matched up and edited, so their ids change on every save — the
    right semantics for the `PUT` behind the edit form, which replaces the whole
    contact anyway.
    """
    contact.addresses = _build_addresses(payloads)


def create_contact(db: Session, payload: ContactCreate) -> Contact:
    data = payload.model_dump()
    data["email"] = _normalize_email(data["email"])
    addresses = payload.addresses
    data.pop("addresses", None)

    contact = Contact(**data, addresses=_build_addresses(addresses))
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def replace_contact(db: Session, contact: Contact, payload: ContactReplace) -> Contact:
    for field, value in payload.model_dump().items():
        if field == "addresses":
            continue  # set from the parsed models below, not the dumped dicts
        setattr(contact, field, _normalize_email(value) if field == "email" else value)

    _set_addresses(contact, payload.addresses)
    db.commit()
    db.refresh(contact)
    return contact


def update_contact(db: Session, contact: Contact, payload: ContactUpdate) -> Contact:
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "addresses":
            continue
        setattr(contact, field, _normalize_email(value) if field == "email" else value)

    # `exclude_unset` is what separates "leave the addresses alone" (key absent)
    # from "remove them all" (key present and empty).
    if "addresses" in payload.model_fields_set and payload.addresses is not None:
        _set_addresses(contact, payload.addresses)

    db.commit()
    db.refresh(contact)
    return contact


def delete_contact(db: Session, contact: Contact) -> None:
    db.delete(contact)
    db.commit()
