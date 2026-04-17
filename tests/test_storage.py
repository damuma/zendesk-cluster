import json
import os
import pytest
from pathlib import Path
from storage import Storage

@pytest.fixture
def tmp_storage(tmp_path):
    return Storage(backend="json", data_dir=str(tmp_path / "data"), config_dir=str(tmp_path / "config"))

def test_save_and_get_ticket(tmp_storage):
    ticket = {"zendesk_id": 1, "subject": "Test", "fase1_resultado": "TECNICO"}
    tmp_storage.save_ticket(ticket)
    tickets = tmp_storage.get_tickets()
    assert len(tickets) == 1
    assert tickets[0]["zendesk_id"] == 1

def test_get_tickets_by_id_returns_dict(tmp_storage):
    tmp_storage.save_ticket({"zendesk_id": 1, "subject": "a"})
    tmp_storage.save_ticket({"zendesk_id": 2, "subject": "b"})
    by_id = tmp_storage.get_tickets_by_id()
    assert by_id[1]["subject"] == "a"
    assert by_id[2]["subject"] == "b"
    assert set(by_id.keys()) == {1, 2}


def test_save_clusters_replaces_whole_list(tmp_storage):
    tmp_storage.save_cluster({"cluster_id": "X", "estado": "abierto"})
    tmp_storage.save_clusters([
        {"cluster_id": "A", "estado": "abierto"},
        {"cluster_id": "B", "estado": "refined"},
    ])
    clusters = tmp_storage.get_clusters()
    assert {c["cluster_id"] for c in clusters} == {"A", "B"}


def test_save_and_get_cluster(tmp_storage):
    cluster = {"cluster_id": "CLU-001", "nombre": "Test cluster", "estado": "abierto"}
    tmp_storage.save_cluster(cluster)
    clusters = tmp_storage.get_clusters()
    assert len(clusters) == 1
    assert clusters[0]["cluster_id"] == "CLU-001"

def test_get_clusters_filters_by_estado(tmp_storage):
    tmp_storage.save_cluster({"cluster_id": "CLU-001", "estado": "abierto"})
    tmp_storage.save_cluster({"cluster_id": "CLU-002", "estado": "cerrado"})
    abiertos = tmp_storage.get_clusters(estado="abierto")
    assert len(abiertos) == 1
    assert abiertos[0]["cluster_id"] == "CLU-001"

def test_save_and_get_conceptos(tmp_storage):
    conceptos = {"version": "1.0", "sistemas": {"stripe": {"keywords": ["stripe"]}}}
    tmp_storage.save_conceptos(conceptos)
    loaded = tmp_storage.get_conceptos()
    assert loaded["version"] == "1.0"
    assert "stripe" in loaded["sistemas"]

def test_upsert_ticket_no_duplicates(tmp_storage):
    tmp_storage.save_ticket({"zendesk_id": 1, "subject": "Original"})
    tmp_storage.save_ticket({"zendesk_id": 1, "subject": "Updated"})
    tickets = tmp_storage.get_tickets()
    assert len(tickets) == 1
    assert tickets[0]["subject"] == "Updated"

def test_upsert_cluster_no_duplicates(tmp_storage):
    tmp_storage.save_cluster({"cluster_id": "CLU-001", "estado": "abierto"})
    tmp_storage.save_cluster({"cluster_id": "CLU-001", "estado": "cerrado"})
    clusters = tmp_storage.get_clusters()
    assert len(clusters) == 1
    assert clusters[0]["estado"] == "cerrado"
