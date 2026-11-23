class EmployeeStorage:
    def __init__(self):
        self.employees = {}

    def add_employee(self, employee):
        if employee.id in self.employees:
            raise ValueError("Employee with this ID already exists.")
        self.employees[employee.id] = employee

    def get_employee(self, employee_id):
        return self.employees.get(employee_id)

    def list_employees(self):
        return list(self.employees.values())

    def remove_employee(self, employee_id):
        if employee_id in self.employees:
            del self.employees[employee_id]
        else:
            raise KeyError("Employee not found.")
