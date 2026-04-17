"""Fase 0.5 — poblar data/zendesk_users.json para los requester_id conocidos.

Dado un ZendeskClient y un ZendeskUsersCache, descarga vía show_many.json los
usuarios que todavía no están en el cache y lo persiste. Marca como null los
usuarios borrados (no devueltos por la API).
"""
from __future__ import annotations


def populate_cache_from_ids(client, cache, requester_ids: list[int]) -> dict:
    ids = [i for i in requester_ids if i is not None]
    missing = cache.missing_ids(ids)
    if not missing:
        return {"fetched": 0, "already_cached": len(ids)}
    users = client.fetch_users_by_ids(missing)
    cache.upsert(users)
    returned_ids = {u["id"] for u in users if u.get("id") is not None}
    deleted = [i for i in missing if i not in returned_ids]
    if deleted:
        cache.upsert([
            {"id": i, "email": None, "name": None, "role": None} for i in deleted
        ])
    cache.save()
    return {"fetched": len(missing), "already_cached": len(ids) - len(missing)}
