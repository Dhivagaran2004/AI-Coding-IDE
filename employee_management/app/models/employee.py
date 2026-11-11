from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Employee:
    employee_id: int
    name: str
    email: str
    department: str
    position: str
    salary: Decimal

    def to_dict(self) -> dict[str, object]:
        return {
            "employee_id": self.employee_id,
            "name": self.name,
            "email": self.email,
            "department": self.department,
            "position": self.position,
            "salary": str(self.salary),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "Employee":
        return cls(
            employee_id=int(value["employee_id"]),
            name=str(value["name"]),
            email=str(value["email"]),
            department=str(value["department"]),
            position=str(value["position"]),
            salary=Decimal(str(value["salary"])),
        )