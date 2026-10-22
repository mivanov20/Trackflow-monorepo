from supplier_directory.repository import SupplierRepository
from supplier_directory.seed import SUPPLIERS_SEED, main, seed_repository

SEED_COUNT = len(SUPPLIERS_SEED)


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


def test_seed_matches_the_brief(tmp_path):
    assert SEED_COUNT == 15
    assert len({entry["name"] for entry in SUPPLIERS_SEED}) == SEED_COUNT
    repo = SupplierRepository(tmp_path / "db.json")
    try:
        seed_repository(repo)
        stored = {s.name: s for s in repo.list()}
    finally:
        repo.close()
    assert {name for name, s in stored.items() if s.status.value == "suspended"} == {"Laser Ship", "SAP WM Cloud"}
    assert stored["MRW España"].currency.value == "EUR"
    assert stored["UPS Ground"].rate_per_shipment == 7.45
    assert stored["Logística Inversa Iberia"].service_zone is None
