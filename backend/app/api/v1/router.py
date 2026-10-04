from fastapi import APIRouter

from app.api.v1 import (
    auth,
    content_entries,
    dashboard,
    delete_requests,
    documents,
    lineages,
    persons,
    rag,
    relations,
    settings,
    tasks,
    tree,
    users,
    visit,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(persons.router)
api_router.include_router(relations.router)
api_router.include_router(tree.router)
api_router.include_router(documents.router)
api_router.include_router(tasks.router)
api_router.include_router(users.router)
api_router.include_router(lineages.router)
api_router.include_router(delete_requests.router)
api_router.include_router(content_entries.router)
api_router.include_router(visit.router)
api_router.include_router(settings.router)
api_router.include_router(rag.router)
