from .employee import Employee

class EmployeeManager:
    def __init__(self):
        self._employees = {}

    def add_employee(self, employee: Employee):
        self._employees[employee.employee_id] = employee

    def get_employee(self, employee_id: int):
        return self._employees.get(employee_id)

    def list_employees(self):
        return list(self._employees.values())