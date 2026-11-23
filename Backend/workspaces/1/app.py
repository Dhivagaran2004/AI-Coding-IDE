from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from database import init_db, get_db
import os

app = Flask(__name__, static_folder='static')
CORS(app)

init_db()

@app.route('/')
def serve_index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/api/departments', methods=['GET'])
def get_departments():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM departments')
    departments = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(departments)

@app.route('/api/employees', methods=['GET'])
def get_employees():
    search = request.args.get('search', '')
    dept_id = request.args.get('department_id', '')
    
    conn = get_db()
    cursor = conn.cursor()
    query = '''
        SELECT e.*, d.name as department_name 
        FROM employees e 
        LEFT JOIN departments d ON e.department_id = d.id 
        WHERE 1=1
    '''
    params = []
    if search:
        query += ' AND (e.name LIKE ? OR e.email LIKE ? OR e.position LIKE ?)'
        params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])
    if dept_id:
        query += ' AND e.department_id = ?'
        params.append(dept_id)
        
    cursor.execute(query, params)
    employees = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(employees)

@app.route('/api/employees', methods=['POST'])
def create_employee():
    data = request.json
    if not data or not all(k in data for k in ('name', 'email', 'position', 'salary', 'hire_date')):
        return jsonify({'error': 'Missing required fields'}), 400
        
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO employees (name, email, position, department_id, salary, hire_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (data['name'], data['email'], data['position'], data.get('department_id'), data['salary'], data['hire_date']))
        conn.commit()
        emp_id = cursor.lastrowid
        conn.close()
        return jsonify({'id': emp_id, 'message': 'Employee created successfully'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/employees/<int:emp_id>', methods=['PUT'])
def update_employee(emp_id):
    data = request.json
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE employees 
            SET name = ?, email = ?, position = ?, department_id = ?, salary = ?, hire_date = ?
            WHERE id = ?
        ''', (data.get('name'), data.get('email'), data.get('position'), data.get('department_id'), data.get('salary'), data.get('hire_date'), emp_id))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Employee updated successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/employees/<int:emp_id>', methods=['DELETE'])
def delete_employee(emp_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM employees WHERE id = ?', (emp_id,))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Employee deleted successfully'})

if __name__ == '__main__':
    app.run(debug=True, port=5000)
