from models import db, Department, Employee

def seed_database(app):
    with app.app_context():
        db.create_all()
        if not Department.query.first():
            dept_hr = Department(name='Human Resources', description='HR and Recruitment')
            dept_eng = Department(name='Engineering', description='Software Development & QA')
            dept_fin = Department(name='Finance', description='Accounting and Payroll')
            db.session.add_all([dept_hr, dept_eng, dept_fin])
            db.session.commit()

            emp1 = Employee(first_name='Alice', last_name='Smith', email='alice.smith@example.com', phone='555-0101', position='HR Manager', salary=75000, hire_date='2022-01-15', status='Active', department_id=dept_hr.id)
            emp2 = Employee(first_name='Bob', last_name='Johnson', email='bob.johnson@example.com', phone='555-0202', position='Senior Software Engineer', salary=110000, hire_date='2021-06-01', status='Active', department_id=dept_eng.id)
            emp3 = Employee(first_name='Charlie', last_name='Brown', email='charlie.brown@example.com', phone='555-0303', position='Accountant', salary=80000, hire_date='2023-03-10', status='Active', department_id=dept_fin.id)
            db.session.add_all([emp1, emp2, emp3])
            db.session.commit()
