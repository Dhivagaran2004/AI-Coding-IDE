# Employee Management System

A small, dependency-free employee management application. It provides a
command-line interface for creating, searching, viewing, updating, and
deleting employee records. Data is stored as JSON and remains available after
the application is restarted.

## Requirements

- Python 3.10 or newer
- `pytest` to run the test suite

No third-party packages are needed to use the application.

## Run the application

Run commands from this directory. The default database is
`data/employees.json`; pass `--data PATH` before the command to select another
JSON file.

```powershell
python main.py add --name "Jordan Lee" --email jordan@example.com --department Engineering --position Developer --salary 85000
python main.py list
python main.py list --search engineering
python main.py show 1
python main.py update 1 --position "Senior Developer" --salary 92000
python main.py delete 1
```

Deletion asks for confirmation. Use `python main.py delete 1 --yes` only when
you intend to skip the interactive confirmation.

## Run tests

```powershell
python -m pytest -q
```

The tests cover validation, CRUD behavior, persistence after reopening the
service, and a command-line end-to-end workflow.