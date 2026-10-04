import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_module_write
from app.models.orm import FileMetadata, User
from app.models.schemas import DocumentLinkPerson, DocumentOut
from app.services import file_service
from app.services.audit_service import log_action
from app.utils.image_utils import sanitize_filename

router = APIRouter(prefix="/documents", tags=["文件"])

ALLOWED_EXTS = {".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def _doc_to_out(doc: FileMetadata) -> DocumentOut:
    out = DocumentOut.model_validate(doc)
    out.url = file_service.public_url(doc.file_path)
    return out


@router.post("/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    title: Optional[str] = None,
    doc_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("documents")),
):
    filename = sanitize_filename(file.filename or "unnamed")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=400, detail=f"不支持的文件类型 {ext}，仅支持 {sorted(ALLOWED_EXTS)}"
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")

    doc_id = file_service.new_doc_id("doc")
    object_name = f"docs/{doc_id}{ext}"
    await file_service.upload_bytes(object_name, data, file.content_type)

    doc = FileMetadata(
        doc_id=doc_id,
        title=title or filename,
        type=doc_type or ext.lstrip("."),
        file_path=object_name,
        file_size=len(data),
        mime_type=file.content_type,
        uploaded_by=current_user.id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    log_action(
        current_user.id, "upload_document", "document", doc_id,
        detail=f"上传文件 {filename}",
    )
    return _doc_to_out(doc)


@router.get("", response_model=List[DocumentOut])
async def list_documents(
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = db.query(FileMetadata).order_by(FileMetadata.created_at.desc())
    if search:
        query = query.filter(FileMetadata.title.ilike(f"%{search}%"))
    docs = query.limit(500).all()
    return [_doc_to_out(d) for d in docs]


@router.get("/{doc_id}", response_model=DocumentOut)
async def get_document(
    doc_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    doc = db.query(FileMetadata).filter(FileMetadata.doc_id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文件不存在")
    return _doc_to_out(doc)


@router.put("/{doc_id}", response_model=DocumentOut)
async def update_document(
    doc_id: str,
    data: DocumentLinkPerson,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("documents")),
):
    doc = db.query(FileMetadata).filter(FileMetadata.doc_id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文件不存在")
    if data.person_id is not None:
        doc.person_id = data.person_id
    if data.title is not None:
        doc.title = data.title
    db.commit()
    db.refresh(doc)
    log_action(current_user.id, "update_document", "document", doc_id)
    return _doc_to_out(doc)


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("documents")),
):
    doc = db.query(FileMetadata).filter(FileMetadata.doc_id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文件不存在")
    db.delete(doc)
    db.commit()
    await file_service.remove_object(doc.file_path)
    log_action(current_user.id, "delete_document", "document", doc_id, "删除文件")
