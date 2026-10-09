import importlib.util
from pathlib import Path
import sqlite3
import pytest

spec=importlib.util.spec_from_file_location('reference_import_tool',Path(__file__).resolve().parents[3]/'tools/import_reference_plays.py')
tool=importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_apply_requires_host_root(monkeypatch,tmp_path):
    monkeypatch.setattr(tool.os,'geteuid',lambda:1000)
    with pytest.raises(RuntimeError,match='root'):
        tool.require_offline(tmp_path/'library.db')


def test_fts_content_mismatch_is_detected():
    db=sqlite3.connect(':memory:')
    db.executescript("CREATE TABLE tracks(item_id INTEGER PRIMARY KEY,search_name TEXT); CREATE VIRTUAL TABLE tracks_fts USING fts5(search_name,content='tracks',content_rowid='item_id'); INSERT INTO tracks VALUES(1,'unindexed track');")
    tool.check_integrity(db)
    with pytest.raises(sqlite3.DatabaseError):tool.check_integrity(db,fts=True)
    db.execute("INSERT INTO tracks_fts(tracks_fts) VALUES('rebuild')");db.commit()
    tool.check_integrity(db,fts=True)
