import re
from decimal import Decimal, InvalidOperation


_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty.")
    if len(normalized) > 120:
        raise ValueError(f"{field_name} must be 120 characters or fewer.")
    return normalized


def validate_email(value: str) -> str:
    normalized = validate_text(value, "Email").casefold()
    if len(normalized) > 254 or not _EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("Enter a valid email address.")
    return normalized


def validate_salary(value: Decimal | str | int | float) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("Salary must be a valid amount.") from None
    if not amount.is_finite() or amount < 0:
        raise ValueError("Salary must be a finite amount greater than or equal to zero.")
    return amount