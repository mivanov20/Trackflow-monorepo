"""El seed de inventario debe terminar con código 1 y un mensaje claro ante fallos críticos."""

from services import seed_data


def test_seed_without_registered_operative_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AUTH_DB_PATH", str(tmp_path / "users.json"))
    assert seed_data.main() == 1
    assert "Register an operative" in capsys.readouterr().err


def test_seed_with_corrupted_account_store_exits_1(tmp_path, monkeypatch, capsys):
    users = tmp_path / "users.json"
    users.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("AUTH_DB_PATH", str(users))
    assert seed_data.main() == 1
    err = capsys.readouterr().err
    assert "Could not read the operative account store" in err
    assert "Traceback" not in err
