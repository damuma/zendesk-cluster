from pathlib import Path
from unittest.mock import patch


def test_reingest_dry_run_does_not_write_or_run_pipeline(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "tickets.json").write_text('[{"zendesk_id": 1}]')
    (tmp_path / "data" / "clusters.json").write_text('[{"cluster_id": "x"}]')

    with patch("scripts.reingest_all.run_pipeline") as run_pl:
        from scripts.reingest_all import main
        rc = main(["--dry-run", "--days", "7"])
    assert rc == 0
    run_pl.assert_not_called()
    assert not list((tmp_path / "data").glob("*.bak-reingest-*"))
    # Archivos originales no tocados
    assert (tmp_path / "data" / "tickets.json").read_text() == '[{"zendesk_id": 1}]'
    assert (tmp_path / "data" / "clusters.json").read_text() == '[{"cluster_id": "x"}]'
    out = capsys.readouterr().out
    assert "DRY-RUN" in out


def test_reingest_real_backups_truncates_and_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "tickets.json").write_text('[{"zendesk_id": 1}]')
    (tmp_path / "data" / "clusters.json").write_text('[{"cluster_id": "y"}]')
    (tmp_path / "data" / "zendesk_users.json").write_text('{"42": {"email": "a@x.com"}}')

    with patch("scripts.reingest_all.run_pipeline") as run_pl:
        from scripts.reingest_all import main
        rc = main(["--days", "7"])
    assert rc == 0
    run_pl.assert_called_once_with(horas=7 * 24, dry_run=False)
    backups = sorted((tmp_path / "data").glob("*.bak-reingest-*"))
    names = {b.name.split(".bak-reingest-")[0] for b in backups}
    assert names == {"tickets.json", "clusters.json"}
    # users cache preservado por defecto
    assert (tmp_path / "data" / "zendesk_users.json").read_text() == '{"42": {"email": "a@x.com"}}'
    assert (tmp_path / "data" / "tickets.json").read_text() == "[]"
    assert (tmp_path / "data" / "clusters.json").read_text() == "[]"


def test_reingest_with_refresh_users_also_purges_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "tickets.json").write_text("[]")
    (tmp_path / "data" / "clusters.json").write_text("[]")
    (tmp_path / "data" / "zendesk_users.json").write_text('{"42": {"email": "a@x.com"}}')

    with patch("scripts.reingest_all.run_pipeline"):
        from scripts.reingest_all import main
        main(["--refresh-users"])
    backups = sorted((tmp_path / "data").glob("*.bak-reingest-*"))
    names = {b.name.split(".bak-reingest-")[0] for b in backups}
    assert "zendesk_users.json" in names
    assert (tmp_path / "data" / "zendesk_users.json").read_text() == "{}"
