class EmployeeDatabase:
    def __init__(self):
        self._data = {}

    def save(self, employee):
        self._data[employee.employee_id] = employee

    def load(self, employee_id):
        return self._data.get(employee_id)