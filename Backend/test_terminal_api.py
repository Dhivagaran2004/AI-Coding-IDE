from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_execute_terminal_command():

    response = client.post(
        "/projects/1/terminal/execute",
        json={
            "command": "python -c \"print('API Test')\""
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["exit_code"] == 0
    assert data["success"] is True
    assert "API Test" in data["stdout"]


def test_empty_terminal_command():

    response = client.post(
        "/projects/1/terminal/execute",
        json={
            "command": ""
        },
    )

    assert response.status_code == 400