import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    first_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(40))

    company: Mapped[str | None] = mapped_column(String(200))
    job_title: Mapped[str | None] = mapped_column(String(200))

    # Base64 `data:` URL rather than a file path: the service is self-contained,
    # so there is no object store to point at. Validated in `schemas.PhotoDataUrl`.
    photo: Mapped[str | None] = mapped_column(Text)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
        nullable=False,
    )

    # `selectin` loads every contact's addresses in one extra query rather than
    # one per contact, and `delete-orphan` means an address removed from this
    # list is deleted rather than left pointing at nothing.
    addresses: Mapped[list["Address"]] = relationship(
        back_populates="contact",
        cascade="all, delete-orphan",
        order_by="Address.id",
        lazy="selectin",
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Contact id={self.id} email={self.email!r}>"


class AddressType(str, enum.Enum):
    """What a given address is for. Stored as its lowercase value."""

    home = "home"
    work = "work"
    other = "other"


class Address(Base):
    """
    One postal address belonging to one contact.

    A contact used to carry a single set of address columns. They live here
    instead, so a contact can have several — a home and a work address, say —
    each labelled with its own `type`.
    """

    __tablename__ = "addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # `ondelete=CASCADE` is enforced by the database (SQLite has
    # `PRAGMA foreign_keys=ON`, see database.py); the ORM relationship's
    # delete-orphan covers the same ground for objects already in a session.
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # A CHECK-constrained string rather than a native enum type, so adding a
    # value later does not need a database migration on PostgreSQL.
    type: Mapped[AddressType] = mapped_column(
        Enum(AddressType, name="address_type", native_enum=False, length=10),
        nullable=False,
        default=AddressType.home,
    )

    street: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(120))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(120))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
        nullable=False,
    )

    contact: Mapped["Contact"] = relationship(back_populates="addresses")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Address id={self.id} contact_id={self.contact_id} type={self.type.value!r}>"
