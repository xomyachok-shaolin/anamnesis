from anamnestic.config import MIGRATIONS_DIR


def test_migrations_are_available_to_runtime():
    names = {p.name for p in MIGRATIONS_DIR.glob("*.sql")}

    assert "000_core_schema.sql" in names
    assert "015_repair_entity_graph_schema.sql" in names
