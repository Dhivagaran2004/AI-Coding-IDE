from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_repository_index_route_exists():
    response = client.post(
        "/projects/999999/index"
    )

    assert response.status_code != 404