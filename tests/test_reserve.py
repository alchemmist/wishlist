from fastapi.testclient import TestClient

from app.main import app, db

client = TestClient(app)


def owner():
    db.execute("INSERT INTO users(sub, username, display_name) VALUES ('sub', 'anton', 'Anton')")
    db.commit()
    with client as http:
        http.cookies.clear()
    # Session is created by writing the user id through the database and a direct session is awkward.
    # Items are inserted as the owner, then reserved without a browser session.
    db.execute("INSERT INTO items(owner_id, title) VALUES (1, 'Keyboard')")
    db.commit()


def test_names_are_unique_and_release_needs_the_same_name():
    owner()
    first = client.post("/api/items/1/reserve", json={"name": "Masha"})
    assert first.status_code == 200
    again = client.post("/api/items/1/reserve", json={"name": "masha"})
    assert again.status_code == 200
    assert again.json()["name"] == "Masha"
    other = client.post("/api/items/1/release", json={"name": "Petr"})
    assert other.status_code == 403
    same = client.post("/api/items/1/release", json={"name": "Masha"})
    assert same.status_code == 200
    assert db.execute("SELECT COUNT(*) FROM guests").fetchone()[0] == 1
