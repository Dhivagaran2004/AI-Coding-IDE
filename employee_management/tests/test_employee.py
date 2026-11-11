from decimal import Decimal

import pytest

from app.models.employee import Employee
from app.utils.validators import validate_email, validate_salary, validate_text


def test_employee_round_trips_to_json_compatible_data():
    employee = Employee(
        employee_id=1,
        name="Sam Rivera",
        email="sam@example.com",
        department="Engineering",
        position="Developer",
        salary=Decimal("87500.50"),
    )

    assert Employee.from_dict(employee.to_dict()) == employee
    assert employee.to_dict()["salary"] == "87500.50"


@pytest.mark.parametrize("value", ["", "   "])
def test_text_fields_cannot_be_empty(value):
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_text(value, "Name")


@pytest.mark.parametrize("value", ["no-at-symbol", "person@", "person @example.com"])
def test_email_must_be_valid(value):
    with pytest.raises(ValueError, match="valid email"):
        validate_email(value)


def test_email_is_normalized():
    assert validate_email("  USER@Example.COM ") == "user@example.com"


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "not-money"])
def test_salary_must_be_finite_and_non_negative(value):
    with pytest.raises(ValueError):
        validate_salary(value)


def test_salary_is_rounded_to_cents():
    assert validate_salary("1200.456") == Decimal("1200.46")