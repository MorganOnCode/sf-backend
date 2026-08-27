"""
vCard 4.0 export (RFC 6350).

A contact is only useful in an address book, so this renders one — or the whole
book — in the format Apple Contacts, Google Contacts and Outlook all import.

Everything the app stores has somewhere to go: the photo becomes `PHOTO`, and
because a contact now has *many* typed addresses, each one becomes its own `ADR`
line carrying its type. That is the shape vCard expected all along.

The fiddly parts of the format are the ones implementations get wrong, so they
are handled explicitly here: escaping, folding long lines on octet boundaries,
and CRLF endings.
"""

from collections.abc import Iterable
from datetime import timezone

from app.models import Contact

CRLF = "\r\n"

# RFC 6350 §3.2: lines are folded so no line is longer than 75 octets, excluding
# the line break. A continuation begins with one space, which counts toward the
# next line's budget.
MAX_LINE_OCTETS = 75

MEDIA_TYPE = "text/vcard; charset=utf-8"


def _escape(value: str) -> str:
    """Escape a TEXT value. The backslash has to go first, or it doubles twice."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _fold(line: str) -> str:
    """
    Fold one logical line to the 75-octet limit.

    Counted in octets rather than characters, because the limit is a byte limit —
    but split between characters, so a multi-byte character is never cut in half.
    """
    if len(line.encode("utf-8")) <= MAX_LINE_OCTETS:
        return line

    folded: list[str] = []
    current = ""
    # The first line gets the full budget; every continuation spends one octet
    # on the leading space that marks it as a continuation.
    budget = MAX_LINE_OCTETS

    for char in line:
        size = len(char.encode("utf-8"))
        if size > budget:
            folded.append(current)
            current = char
            budget = MAX_LINE_OCTETS - 1 - size
        else:
            current += char
            budget -= size

    folded.append(current)
    return f"{CRLF} ".join(folded)


def _line(name: str, value: str, **params: str) -> str:
    """One `NAME;PARAM=value:value` line, already folded."""
    prefix = name
    for key, param in params.items():
        prefix += f";{key.upper()}={param}"
    return _fold(f"{prefix}:{value}")


def _contact_lines(contact: Contact) -> Iterable[str]:
    yield "BEGIN:VCARD"
    yield "VERSION:4.0"
    yield "KIND:individual"

    # An opaque but stable identifier, so re-importing updates the same card
    # rather than adding a duplicate.
    yield _line("UID", f"sf-contacts:{contact.id}")

    yield _line("FN", _escape(contact.full_name))
    # N is a structured value: family;given;additional;prefixes;suffixes.
    yield _line("N", f"{_escape(contact.last_name)};{_escape(contact.first_name)};;;")

    if contact.email:
        yield _line("EMAIL", _escape(contact.email), type="work")
    if contact.phone:
        yield _line("TEL", _escape(contact.phone), type="voice")
    if contact.company:
        yield _line("ORG", _escape(contact.company))
    if contact.job_title:
        yield _line("TITLE", _escape(contact.job_title))

    # The point of the exercise: one ADR per address, each carrying its type.
    # ADR is structured too: po-box;extended;street;locality;region;code;country.
    for address in contact.addresses:
        parts = ";".join(
            _escape(part or "")
            for part in (
                "",
                "",
                address.street,
                address.city,
                address.state,
                address.postal_code,
                address.country,
            )
        )
        yield _line("ADR", parts, type=address.type.value)

    if contact.notes:
        yield _line("NOTE", _escape(contact.notes))

    if contact.photo:
        # A URI-valued property: the stored `data:` URL goes through as-is. Its
        # own `;` and `,` are part of the URI syntax and are not TEXT escapes.
        yield _line("PHOTO", contact.photo)

    updated = contact.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    yield _line("REV", updated.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))

    yield "END:VCARD"


def render_vcard(contacts: Iterable[Contact]) -> str:
    """Render one or more contacts as a single vCard document."""
    lines: list[str] = []
    for contact in contacts:
        lines.extend(_contact_lines(contact))
    # A trailing break: every line, including the last, ends with CRLF.
    return CRLF.join(lines) + CRLF if lines else ""


def filename_for(contacts: list[Contact]) -> str:
    """A download name: the person's own for one card, the book's for many."""
    if len(contacts) == 1:
        slug = "".join(
            char if char.isascii() and char.isalnum() else "-"
            for char in contacts[0].full_name.lower()
        ).strip("-")
        return f"{slug or 'contact'}.vcf"
    return "contacts.vcf"
