from models import Employee
from storage import EmployeeStorage

def main():
    storage = EmployeeStorage()
    # Demo: add employees
    e1 = Employee(1, "Alice", "Engineer")
    e2 = Employee(2, "Bob", "Manager")
    storage.add_employee(e1)
    storage.add_employee(e2)

    print("All employees:")
    for emp in storage.list_employees():
        print(emp)

    print("\nGet employee with ID 1:")
    print(storage.get_employee(1))

    print("\nRemove employee with ID 2:")
    storage.remove_employee(2)
    print(storage.list_employees())

if __name__ == "__main__":
    main()
