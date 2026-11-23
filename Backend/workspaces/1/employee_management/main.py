from employee_management.manager import EmployeeManager
from employee_management.employee import Employee

def main():
    manager = EmployeeManager()
    emp1 = Employee(1, "Alice", "Developer", 70000)
    emp2 = Employee(2, "Bob", "Designer", 65000)
    manager.add_employee(emp1)
    manager.add_employee(emp2)

    for emp in manager.list_employees():
        print(emp)

if __name__ == "__main__":
    main()