import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Mapping, TypedDict, cast

from app.models.employee import Employee
from app.utils.validators import validate_email, validate_salary, validate_text


class EmployeeFields(TypedDict):
    name: str
    email: str
    department: str
    position: str
    salary: Decimal


SalaryInput = Decimal | str | int | float


class EmployeeNotFoundError(LookupError):
    pass


class DuplicateEmployeeError(ValueError):
    pass


class EmployeeService:
    def __init__(self, storage_path: str | Path):
        self.storage_path = Path(storage_path)
        self._lock = RLock()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._employees = self._load()

    def add_employee(
        self,
        *,
        name: str,
        email: str,
        department: str,
        position: str,
        salary: Decimal | str | int | float,
    ) -> Employee:
        with self._lock:
            values = self._validated_fields({
                "name": name,
                "email": email,
                "department": department,
                "position": position,
                "salary": salary,
            })
            if any(employee.email == values["email"] for employee in self._employees.values()):
                raise DuplicateEmployeeError(
                    f"An employee with email {values['email']} already exists."
                )
            employee_id = max(self._employees, default=0) + 1
            employee = Employee(employee_id=employee_id, **values)
            employees = {**self._employees, employee_id: employee}
            self._save(employees)
            self._employees = employees
            return employee

    def get_employee(self, employee_id: int) -> Employee:
        with self._lock:
            try:
                return self._employees[employee_id]
            except KeyError:
                raise EmployeeNotFoundError(
                    f"Employee {employee_id} was not found."
                ) from None

    def list_employees(self, search: str = "") -> list[Employee]:
        with self._lock:
            employees = sorted(self._employees.values(), key=lambda item: item.employee_id)
            query = search.strip().casefold()
            if not query:
                return employees
            return [
                employee
                for employee in employees
                if query in " ".join((
                    employee.name,
                    employee.email,
                    employee.department,
                    employee.position,
                )).casefold()
            ]

    def update_employee(
        self,
        employee_id: int,
        updates: Mapping[str, str | Decimal | int | float],
    ) -> Employee:
        allowed_fields = {"name", "email", "department", "position", "salary"}
        if not updates:
            raise ValueError("Provide at least one field to update.")
        unsupported = set(updates) - allowed_fields
        if unsupported:
            raise ValueError(f"Unsupported employee field(s): {', '.join(sorted(unsupported))}.")
        with self._lock:
            employee = self.get_employee(employee_id)
            current = employee.to_dict()
            current.update(updates)
            values = self._validated_fields(current)
            if any(
                other.employee_id != employee_id and other.email == values["email"]
                for other in self._employees.values()
            ):
                raise DuplicateEmployeeError(
                    f"An employee with email {values['email']} already exists."
                )
            updated = Employee(employee_id=employee_id, **values)
            employees = {**self._employees, employee_id: updated}
            self._save(employees)
            self._employees = employees
            return updated

    def delete_employee(self, employee_id: int) -> Employee:
        with self._lock:
            employee = self.get_employee(employee_id)
            employees = dict(self._employees)
            del employees[employee_id]
            self._save(employees)
            self._employees = employees
            return employee

    @staticmethod
    def _validated_fields(values: Mapping[str, object]) -> EmployeeFields:
        return {
            "name": validate_text(str(values["name"]), "Name"),
            "email": validate_email(str(values["email"])),
            "department": validate_text(str(values["department"]), "Department"),
            "position": validate_text(str(values["position"]), "Position"),
            "salary": validate_salary(cast(SalaryInput, values["salary"])),
        }

    def _load(self) -> dict[int, Employee]:
        if not self.storage_path.exists():
            return {}
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("The employee data must be a JSON list.")
            employees = [Employee.from_dict(item) for item in payload]
            if len({employee.employee_id for employee in employees}) != len(employees):
                raise ValueError("Employee data contains duplicate IDs.")
            if len({employee.email.casefold() for employee in employees}) != len(employees):
                raise ValueError("Employee data contains duplicate email addresses.")
            return {employee.employee_id: employee for employee in employees}
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"Could not load employee data from {self.storage_path}: {error}"
            ) from error

    def _save(self, employees: Mapping[int, Employee]) -> None:
        payload = [
            employee.to_dict()
            for employee in sorted(
                employees.values(),
                key=lambda item: item.employee_id,
            )
        ]
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.storage_path.parent,
                prefix=f".{self.storage_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = temporary_file.name
                json.dump(payload, temporary_file, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.storage_path)
        except OSError:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
            raise