import json
from decimal import Decimal

import pytest

from app.services import employee_service
from app.services.employee_service import (
    DuplicateEmployeeError,
    EmployeeNotFoundError,
    EmployeeService,
)


def add_employee(service, email="alex@example.com", name="Alex Morgan"):
    return service.add_employee(
        name=name,
        email=email,
        department="Operations",
        position="Coordinator",
        salary=Decimal("62000"),
    )


def test_employee_crud_persists_across_service_instances(tmp_path):
    storage = tmp_path / "employees.json"
    service = EmployeeService(storage)
    created = add_employee(service)

    updated = service.update_employee(
        created.employee_id,
        {"position": "Senior Coordinator", "salary": "70000.25"},
    )
    assert updated.position == "Senior Coordinator"
    assert updated.salary == Decimal("70000.25")

    reopened = EmployeeService(storage)
    assert reopened.get_employee(created.employee_id) == updated
    assert reopened.list_employees("operations") == [updated]
    assert reopened.list_employees("not found") == []

    deleted = reopened.delete_employee(created.employee_id)
    assert deleted == updated
    assert EmployeeService(storage).list_employees() == []


def test_employee_ids_increase_without_reusing_deleted_ids(tmp_path):
    service = EmployeeService(tmp_path / "employees.json")
    first = add_employee(service)
    second = add_employee(service, "taylor@example.com", "Taylor Lee")
    service.delete_employee(first.employee_id)

    third = add_employee(service, "jordan@example.com", "Jordan Kim")

    assert [second.employee_id, third.employee_id] == [2, 3]


def test_duplicate_email_is_rejected_case_insensitively(tmp_path):
    service = EmployeeService(tmp_path / "employees.json")
    add_employee(service)

    with pytest.raises(DuplicateEmployeeError, match="already exists"):
        add_employee(service, "ALEX@example.com", "Another Person")


def test_update_cannot_assign_another_employees_email(tmp_path):
    service = EmployeeService(tmp_path / "employees.json")
    first = add_employee(service)
    second = add_employee(service, "taylor@example.com", "Taylor Lee")

    with pytest.raises(DuplicateEmployeeError):
        service.update_employee(second.employee_id, {"email": first.email})


def test_missing_employee_and_empty_updates_are_reported(tmp_path):
    service = EmployeeService(tmp_path / "employees.json")

    with pytest.raises(EmployeeNotFoundError):
        service.get_employee(42)
    with pytest.raises(ValueError, match="at least one field"):
        service.update_employee(42, {})


def test_invalid_update_does_not_change_persisted_data(tmp_path):
    storage = tmp_path / "employees.json"
    service = EmployeeService(storage)
    employee = add_employee(service)

    with pytest.raises(ValueError, match="valid email"):
        service.update_employee(employee.employee_id, {"email": "invalid"})

    assert EmployeeService(storage).get_employee(employee.employee_id) == employee


def test_failed_save_does_not_change_memory_or_persisted_employee(tmp_path, monkeypatch):
    storage = tmp_path / "employees.json"
    service = EmployeeService(storage)
    employee = add_employee(service)

    def fail_replace(_source, _destination):
        assert _source != _destination
        raise OSError("disk is unavailable")

    monkeypatch.setattr(employee_service.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk is unavailable"):
        service.update_employee(employee.employee_id, {"position": "Manager"})

    assert service.get_employee(employee.employee_id) == employee
    assert EmployeeService(storage).get_employee(employee.employee_id) == employee


def test_corrupt_storage_is_not_silently_treated_as_empty(tmp_path):
    storage = tmp_path / "employees.json"
    storage.write_text("{invalid", encoding="utf-8")

    with pytest.raises(ValueError, match="Could not load employee data"):
        EmployeeService(storage)


def test_cli_end_to_end_add_search_update_delete_and_reopen(tmp_path, capsys):
    from main import main

    storage = tmp_path / "employees.json"

    assert main([
        "--data", str(storage), "add",
        "--name", "Casey Park",
        "--email", "casey@example.com",
        "--department", "Finance",
        "--position", "Analyst",
        "--salary", "74000",
    ]) == 0
    assert "Created employee 1." in capsys.readouterr().out

    assert main(["--data", str(storage), "list", "--search", "finance"]) == 0
    assert "Casey Park" in capsys.readouterr().out

    assert main([
        "--data", str(storage), "update", "1", "--position", "Senior Analyst",
    ]) == 0
    assert "Senior Analyst" in capsys.readouterr().out

    assert main(["--data", str(storage), "show", "1"]) == 0
    assert "Senior Analyst" in capsys.readouterr().out

    assert main(["--data", str(storage), "delete", "1", "--yes"]) == 0
    assert "Deleted employee 1." in capsys.readouterr().out
    assert json.loads(storage.read_text(encoding="utf-8")) == []


def test_cli_delete_requires_confirmation(tmp_path, monkeypatch, capsys):
    from main import main

    storage = tmp_path / "employees.json"
    service = EmployeeService(storage)
    add_employee(service)

    def decline(prompt):
        assert "Delete Alex Morgan" in prompt
        return "no"

    monkeypatch.setattr("builtins.input", decline)

    assert main(["--data", str(storage), "delete", "1"]) == 1
    assert "Deletion cancelled." in capsys.readouterr().out
    assert len(EmployeeService(storage).list_employees()) == 1