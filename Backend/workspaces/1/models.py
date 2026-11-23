from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Department(db.Model):
    __tablename__ = 'departments'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255))
    employees = db.relationship('Employee', backref='department', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'employee_count': len(self.employees)
        }

class Employee(db.Model):
    __tablename__ = 'employees'
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    position = db.Column(db.String(100), nullable=False)
    salary = db.Column(db.Float, nullable=False)
    hire_date = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='Active')
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'email': self.email,
            'phone': self.phone,
            'position': self.position,
            'salary': self.salary,
            'hire_date': self.hire_date,
            'status': self.status,
            'department_id': self.department_id,
            'department_name': self.department.name if self.department else None
        }
