import json
import os
import pytest
from pathlib import Path
from storage import Storage

@pytest.fixture
def tmp_storage(tmp_path):
    return Storage(backend="json", data_dir=str(tmp_path))

def test_save_and_get_ticket(tmp_storage):
    ticket = {"zendesk_id": 1, "subject": "Test", "fase1_resultado": "TECNICO"}
    tmp_storage.save_ticket(ticket)
    tickets = tmp_storage.get_tickets()
    assert len(tickets) == 1
    assert tickets[0]["zendesk_id"] == 1

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
