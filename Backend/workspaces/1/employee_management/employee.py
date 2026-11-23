class Employee:
    def __init__(self, employee_id: int, name: str, position: str, salary: float):
        self.employee_id = employee_id
        self.name = name
        self.position = position
        self.salary = salary

    def __repr__(self):
        return f"Employee(id={self.employee_id}, name={self.name}, position={self.position}, salary={self.salary})"