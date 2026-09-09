"""Email normalization policy for identity uniqueness and login."""

from email_validator import EmailNotValidError, validate_email

from klack.modules.identity.domain.errors import InvalidEmailAddress


def normalize_email(value: str) -> str:
    """Return the product's case-insensitive canonical mailbox representation."""
    try:
        validated = validate_email(value.strip(), check_deliverability=False)
        return f"{validated.local_part}@{validated.ascii_domain}".casefold()
    except EmailNotValidError as exc:
        raise InvalidEmailAddress from exc
