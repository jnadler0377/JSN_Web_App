# app/services/document_manager_service.py
"""
Document Management System for JSN Holdings
- Version tracking
- Checksums & duplicate detection
- Metadata extraction
- Thumbnail generation
"""

from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
import hashlib
import json
import logging
import os

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class DocumentType(Enum):
    """Document type categories"""
    VERIFIED_COMPLAINT = "verified_complaint"
    MORTGAGE = "mortgage"
    DEED_CURRENT = "current_deed"
    DEED_PREVIOUS = "previous_deed"
    VALUE_CALC = "value_calc"
    APPRAISAL = "appraisal"
    TITLE_REPORT = "title_report"
    INSPECTION_REPORT = "inspection_report"
    PHOTOS = "photos"
    CONTRACT = "contract"
    LIEN = "lien"
    TAX_RECORD = "tax_record"
    OTHER = "other"

    @classmethod
    def from_string(cls, value: str) -> "DocumentType":
        """Convert string to DocumentType"""
        try:
            return cls(value)
        except ValueError:
            return cls.OTHER
    
    @classmethod
    def display_name(cls, doc_type: "DocumentType") -> str:
        """Get human-readable name"""
        names = {
            cls.VERIFIED_COMPLAINT: "Verified Complaint",
            cls.MORTGAGE: "Mortgage",
            cls.DEED_CURRENT: "Current Deed",
            cls.DEED_PREVIOUS: "Previous Deed",
            cls.VALUE_CALC: "Value Calculation",
            cls.APPRAISAL: "Appraisal",
            cls.TITLE_REPORT: "Title Report",
            cls.INSPECTION_REPORT: "Inspection Report",
            cls.PHOTOS: "Photos",
            cls.CONTRACT: "Contract",
            cls.LIEN: "Lien Document",
            cls.TAX_RECORD: "Tax Record",
            cls.OTHER: "Other",
        }
        return names.get(doc_type, "Other")


# SQL to create the case_documents table
CREATE_DOCUMENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS case_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    document_type VARCHAR(50) NOT NULL,
    filename VARCHAR(255) NOT NULL,
    original_filename VARCHAR(255),
    file_path VARCHAR(500) NOT NULL,
    file_size INTEGER DEFAULT 0,
    file_hash VARCHAR(64),
    mime_type VARCHAR(100),
    version INTEGER DEFAULT 1,
    thumbnail_path VARCHAR(500),
    metadata_json TEXT,
    description TEXT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uploaded_by_user_id INTEGER,
    is_deleted BOOLEAN DEFAULT 0,
    deleted_at TIMESTAMP,
    FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
)
"""

CREATE_DOCUMENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_case_documents_case_id ON case_documents(case_id)",
    "CREATE INDEX IF NOT EXISTS idx_case_documents_type ON case_documents(document_type)",
    "CREATE INDEX IF NOT EXISTS idx_case_documents_hash ON case_documents(file_hash)",
]


class DocumentManager:
    """Advanced document management with versioning"""
    
    def __init__(self, base_path: str, db: Session):
        self.base_path = Path(base_path)
        self.db = db
        self._ensure_table_exists()
    
    def _ensure_table_exists(self):
        """Create documents table if not exists"""
        try:
            # Create table
            self.db.execute(text(CREATE_DOCUMENTS_TABLE_SQL))
            self.db.commit()
            
            # Create indexes separately
            for index_sql in CREATE_DOCUMENTS_INDEXES:
                try:
                    self.db.execute(text(index_sql))
                    self.db.commit()
                except Exception:
                    self.db.rollback()
                    
        except Exception as e:
            logger.warning(f"Table creation warning (may already exist): {e}")
            self.db.rollback()
    
    def calculate_hash(self, content: bytes) -> str:
        """Calculate SHA256 hash of file content"""
        return hashlib.sha256(content).hexdigest()
    
    def find_duplicate(self, case_id: int, file_hash: str) -> Optional[Dict[str, Any]]:
        """Check if document with same hash already exists for this case"""
        result = self.db.execute(
            text("""
                SELECT id, filename, document_type, version, uploaded_at
                FROM case_documents
                WHERE case_id = :case_id AND file_hash = :file_hash AND is_deleted = 0
                LIMIT 1
            """),
            {"case_id": case_id, "file_hash": file_hash}
        ).fetchone()
        
        if result:
            return {
                "id": result[0],
                "filename": result[1],
                "document_type": result[2],
                "version": result[3],
                "uploaded_at": result[4],
            }
        return None
    
    def get_next_version(self, case_id: int, doc_type: DocumentType) -> int:
        """Get next version number for this document type"""
        result = self.db.execute(
            text("""
                SELECT MAX(version) FROM case_documents
                WHERE case_id = :case_id AND document_type = :doc_type AND is_deleted = 0
            """),
            {"case_id": case_id, "doc_type": doc_type.value}
        ).fetchone()
        
        current_max = result[0] if result and result[0] else 0
        return current_max + 1
    
    def extract_metadata(self, file_path: Path, mime_type: str) -> Dict[str, Any]:
        """Extract metadata from file"""
        metadata = {
            "file_size": file_path.stat().st_size if file_path.exists() else 0,
            "extracted_at": datetime.utcnow().isoformat(),
        }
        
        # Add PDF-specific metadata if applicable
        if mime_type == "application/pdf":
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(str(file_path))
                metadata["page_count"] = doc.page_count
                metadata["pdf_metadata"] = doc.metadata
                doc.close()
            except Exception as e:
                logger.warning(f"Could not extract PDF metadata: {e}")
        
        return metadata
    
    def upload_document(
        self,
        case_id: int,
        filename: str,
        content: bytes,
        doc_type: DocumentType,
        mime_type: str,
        user_id: Optional[int] = None,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Upload document with version tracking and duplicate detection
        
        Returns:
            Dict with status and document info
        """
        # Calculate file hash
        file_hash = self.calculate_hash(content)
        
        # Check for duplicates
        existing = self.find_duplicate(case_id, file_hash)
        if existing:
            return {
                "status": "duplicate",
                "message": "This file has already been uploaded",
                "existing_document": existing
            }
        
        # Create case folder
        case_folder = self.base_path / str(case_id)
        case_folder.mkdir(parents=True, exist_ok=True)
        
        # Get next version
        version = self.get_next_version(case_id, doc_type)
        
        # Create versioned filename
        base_name = Path(filename).stem
        extension = Path(filename).suffix
        versioned_filename = f"{doc_type.value}_v{version}_{base_name}{extension}"
        file_path = case_folder / versioned_filename
        
        # Save file
        try:
            with open(file_path, "wb") as f:
                f.write(content)
        except Exception as e:
            logger.error(f"Failed to save file: {e}")
            return {"status": "error", "message": f"Failed to save file: {e}"}
        
        # Extract metadata
        metadata = self.extract_metadata(file_path, mime_type)
        
        # Save to database
        try:
            self.db.execute(
                text("""
                    INSERT INTO case_documents (
                        case_id, document_type, filename, original_filename,
                        file_path, file_size, file_hash, mime_type, version,
                        metadata_json, description, uploaded_at, uploaded_by_user_id
                    ) VALUES (
                        :case_id, :document_type, :filename, :original_filename,
                        :file_path, :file_size, :file_hash, :mime_type, :version,
                        :metadata_json, :description, :uploaded_at, :uploaded_by_user_id
                    )
                """),
                {
                    "case_id": case_id,
                    "document_type": doc_type.value,
                    "filename": versioned_filename,
                    "original_filename": filename,
                    "file_path": str(file_path.relative_to(self.base_path)),
                    "file_size": len(content),
                    "file_hash": file_hash,
                    "mime_type": mime_type,
                    "version": version,
                    "metadata_json": json.dumps(metadata),
                    "description": description,
                    "uploaded_at": datetime.utcnow(),
                    "uploaded_by_user_id": user_id,
                }
            )
            self.db.commit()
            
            # Get the inserted ID
            result = self.db.execute(text("SELECT last_insert_rowid()")).fetchone()
            doc_id = result[0] if result else None
            
            return {
                "status": "success",
                "document": {
                    "id": doc_id,
                    "case_id": case_id,
                    "document_type": doc_type.value,
                    "filename": versioned_filename,
                    "original_filename": filename,
                    "file_path": str(file_path),
                    "file_size": len(content),
                    "version": version,
                    "uploaded_at": datetime.utcnow().isoformat(),
                }
            }
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to save document record: {e}")
            # Clean up the file
            if file_path.exists():
                file_path.unlink()
            return {"status": "error", "message": f"Failed to save document record: {e}"}
    
    def get_documents_for_case(self, case_id: int, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """Get all documents for a case"""
        query = """
            SELECT id, case_id, document_type, filename, original_filename,
                   file_path, file_size, file_hash, mime_type, version,
                   thumbnail_path, metadata_json, description,
                   uploaded_at, uploaded_by_user_id, is_deleted
            FROM case_documents
            WHERE case_id = :case_id
        """
        if not include_deleted:
            query += " AND is_deleted = 0"
        query += " ORDER BY document_type, version DESC"
        
        rows = self.db.execute(text(query), {"case_id": case_id}).fetchall()
        
        documents = []
        for row in rows:
            documents.append({
                "id": row[0],
                "case_id": row[1],
                "document_type": row[2],
                "document_type_display": DocumentType.display_name(DocumentType.from_string(row[2])),
                "filename": row[3],
                "original_filename": row[4],
                "file_path": row[5],
                "file_size": row[6],
                "file_size_display": self._format_file_size(row[6]),
                "file_hash": row[7],
                "mime_type": row[8],
                "version": row[9],
                "thumbnail_path": row[10],
                "metadata": json.loads(row[11]) if row[11] else {},
                "description": row[12],
                "uploaded_at": row[13],
                "uploaded_by_user_id": row[14],
                "is_deleted": row[15],
            })
        
        return documents
    
    def get_document_history(self, case_id: int, doc_type: DocumentType) -> List[Dict[str, Any]]:
        """Get all versions of a document type for a case"""
        rows = self.db.execute(
            text("""
                SELECT id, filename, original_filename, file_size, version,
                       uploaded_at, uploaded_by_user_id, description
                FROM case_documents
                WHERE case_id = :case_id AND document_type = :doc_type AND is_deleted = 0
                ORDER BY version DESC
            """),
            {"case_id": case_id, "doc_type": doc_type.value}
        ).fetchall()
        
        return [
            {
                "id": row[0],
                "filename": row[1],
                "original_filename": row[2],
                "file_size": row[3],
                "file_size_display": self._format_file_size(row[3]),
                "version": row[4],
                "uploaded_at": row[5],
                "uploaded_by_user_id": row[6],
                "description": row[7],
            }
            for row in rows
        ]
    
    def get_document_by_id(self, doc_id: int) -> Optional[Dict[str, Any]]:
        """Get single document by ID"""
        row = self.db.execute(
            text("""
                SELECT id, case_id, document_type, filename, original_filename,
                       file_path, file_size, mime_type, version, description,
                       uploaded_at, is_deleted
                FROM case_documents
                WHERE id = :doc_id
            """),
            {"doc_id": doc_id}
        ).fetchone()
        
        if not row:
            return None
        
        return {
            "id": row[0],
            "case_id": row[1],
            "document_type": row[2],
            "filename": row[3],
            "original_filename": row[4],
            "file_path": row[5],
            "file_size": row[6],
            "mime_type": row[7],
            "version": row[8],
            "description": row[9],
            "uploaded_at": row[10],
            "is_deleted": row[11],
        }
    
    def soft_delete_document(self, doc_id: int) -> bool:
        """Soft delete a document (mark as deleted)"""
        try:
            self.db.execute(
                text("""
                    UPDATE case_documents
                    SET is_deleted = 1, deleted_at = :deleted_at
                    WHERE id = :doc_id
                """),
                {"doc_id": doc_id, "deleted_at": datetime.utcnow()}
            )
            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to delete document: {e}")
            return False
    
    def get_documents_by_type(self, case_id: int) -> Dict[str, List[Dict[str, Any]]]:
        """Get documents grouped by type"""
        all_docs = self.get_documents_for_case(case_id)
        
        grouped = {}
        for doc in all_docs:
            doc_type = doc["document_type"]
            if doc_type not in grouped:
                grouped[doc_type] = []
            grouped[doc_type].append(doc)
        
        return grouped
    
    def get_document_stats(self, case_id: int) -> Dict[str, Any]:
        """Get document statistics for a case"""
        row = self.db.execute(
            text("""
                SELECT 
                    COUNT(*) as total_count,
                    SUM(file_size) as total_size,
                    COUNT(DISTINCT document_type) as type_count
                FROM case_documents
                WHERE case_id = :case_id AND is_deleted = 0
            """),
            {"case_id": case_id}
        ).fetchone()
        
        return {
            "total_count": row[0] or 0,
            "total_size": row[1] or 0,
            "total_size_display": self._format_file_size(row[1] or 0),
            "type_count": row[2] or 0,
        }
    
    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size in human-readable form"""
        if not size_bytes:
            return "0 B"
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"


# Utility function to get document manager
def get_document_manager(db: Session, upload_root: str = "uploads") -> DocumentManager:
    """Factory function to create DocumentManager instance"""
    return DocumentManager(upload_root, db)
