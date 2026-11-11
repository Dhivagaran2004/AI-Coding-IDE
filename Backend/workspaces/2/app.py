from flask import Flask, jsonify, render_template

app = Flask(__name__)

employees = [
    {"id": 1, "name": "Alice", "position": "Engineer"},
    {"id": 2, "name": "Bob", "position": "Manager"},
]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/employees")
def get_employees():
    return jsonify(employees)

if __name__ == "__main__":
    app.run(debug=True)