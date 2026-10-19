import json

from supplier_directory.repository import SERVICE_ROOT, SupplierRepository
from supplier_directory.seed import main, seed_repository

SEED_COUNT = len(json.loads((SERVICE_ROOT / "data" / "suppliers_seed.json").read_text(encoding="utf-8")))


def test_seeder_inserts_once_without_duplicates(tmp_path):
    repo = SupplierRepository(tmp_path / "db.json")
    try:
        assert seed_repository(repo) == SEED_COUNT
        assert seed_repository(repo) == 0
        assert len(repo.list()) == SEED_COUNT
    finally:
        repo.close()


def test_seed_command_reports_count_and_is_idempotent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SUPPLIERS_DB_PATH", str(tmp_path / "db.json"))
    main()
    assert f"{SEED_COUNT} supplier(s) inserted" in capsys.readouterr().out
    main()
    assert "0 supplier(s) inserted" in capsys.readouterr().out


def test_seed_data_has_both_statuses_and_countries():
    entries = json.loads((SERVICE_ROOT / "data" / "suppliers_seed.json").read_text(encoding="utf-8"))
    assert {e["status"] for e in entries} == {"active", "suspended"}
    assert {e["country"] for e in entries} == {"United States", "Spain"}
