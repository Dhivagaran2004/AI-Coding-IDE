import argparse
import json
from pathlib import Path
from typing import Sequence

from app.services.employee_service import (
    DuplicateEmployeeError,
    EmployeeNotFoundError,
    EmployeeService,
)


DEFAULT_STORAGE_PATH = Path(__file__).parent / "data" / "employees.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage employee records stored in a local JSON file."
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_STORAGE_PATH,
        help=f"employee data file (default: {DEFAULT_STORAGE_PATH})",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="add an employee")
    _add_employee_fields(add, required=True)

    commands.add_parser("list", help="list employees").add_argument(
        "--search",
        default="",
        help="filter by name, email, department, or position",
    )

    show = commands.add_parser("show", help="show one employee")
    show.add_argument("employee_id", type=int)

    update = commands.add_parser("update", help="update an employee")
    update.add_argument("employee_id", type=int)
    _add_employee_fields(update, required=False)

    delete = commands.add_parser("delete", help="delete an employee")
    delete.add_argument("employee_id", type=int)
    delete.add_argument(
        "--yes",
        action="store_true",
        help="confirm deletion without an interactive prompt",
    )
    return parser


def _add_employee_fields(
    parser: argparse.ArgumentParser,
    *,
    required: bool,
) -> None:
    for field_name in ("name", "email", "department", "position", "salary"):
        parser.add_argument(
            f"--{field_name}",
            required=required,
            type=float if field_name == "salary" else str,
        )


def _format_employee(employee) -> str:
    return json.dumps(employee.to_dict(), indent=2)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    service = EmployeeService(args.data)
    try:
        if args.command == "add":
            employee = service.add_employee(
                name=args.name,
                email=args.email,
                department=args.department,
                position=args.position,
                salary=args.salary,
            )
            print(f"Created employee {employee.employee_id}.")
            print(_format_employee(employee))
        elif args.command == "list":
            employees = service.list_employees(args.search)
            if not employees:
                print("No employees found.")
            else:
                for employee in employees:
                    print(
                        f"{employee.employee_id}\t{employee.name}\t"
                        f"{employee.email}\t{employee.department}\t"
                        f"{employee.position}\t{employee.salary:.2f}"
                    )
        elif args.command == "show":
            print(_format_employee(service.get_employee(args.employee_id)))
        elif args.command == "update":
            updates = {
                field: getattr(args, field)
                for field in ("name", "email", "department", "position", "salary")
                if getattr(args, field) is not None
            }
            employee = service.update_employee(args.employee_id, updates)
            print(f"Updated employee {employee.employee_id}.")
            print(_format_employee(employee))
        elif args.command == "delete":
            employee = service.get_employee(args.employee_id)
            if not args.yes:
                answer = input(
                    f"Delete {employee.name} (employee {employee.employee_id})? [y/N] "
                )
                if answer.strip().casefold() not in {"y", "yes"}:
                    print("Deletion cancelled.")
                    return 1
            service.delete_employee(args.employee_id)
            print(f"Deleted employee {args.employee_id}.")
        return 0
    except (DuplicateEmployeeError, EmployeeNotFoundError, OSError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())