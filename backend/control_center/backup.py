from __future__ import annotations
import hashlib, json, os, sqlite3, zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from sqlalchemy import select
from backend.database.session import session_scope
from backend.control_center.models import HumanControlBackupModel, HumanControlRestoreRunModel
from backend.control_center.backup_schemas import HumanControlBackupCreate, HumanControlRestoreRequest
from backend.control_center.service import HumanControlConflict, HumanControlNotFound

def _canon(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

class HumanControlBackupService:
    def __init__(self, *, event_bus, backup_dir: str | None = None):
        self.event_bus = event_bus
        self.backup_dir = Path(backup_dir or os.getenv("AI_STUDIO_HUMAN_CONTROL_BACKUP_DIR", "data/human_control_backups"))
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, Any]:
        with session_scope() as s:
            total=len(s.scalars(select(HumanControlBackupModel)).all())
            restores=len(s.scalars(select(HumanControlRestoreRunModel)).all())
        return {"service":"human_control_backup","ready":True,"backup_dir":str(self.backup_dir),"backups":total,"restore_runs":restores}

    async def create_backup(self, req: HumanControlBackupCreate) -> dict[str, Any]:
        with session_scope() as s:
            if s.scalar(select(HumanControlBackupModel).where(HumanControlBackupModel.backup_key==req.backup_key)):
                raise HumanControlConflict("Backup key already exists.")
        stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target=self.backup_dir/f"{req.backup_key}-{stamp}.zip"
        manifest={"version":"1.0","backup_key":req.backup_key,"workspace_id":req.workspace_id,"created_at":datetime.now(timezone.utc).isoformat(),"include_database":req.include_database,"include_evidence":req.include_evidence,"metadata":req.metadata}
        with zipfile.ZipFile(target,"w",zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", _canon(manifest))
            db_url=os.getenv("DATABASE_URL", "")
            db_path=None
            if req.include_database and db_url.startswith("sqlite:///"):
                db_path=Path(db_url.removeprefix("sqlite:///"))
            elif req.include_database:
                candidate=Path("data/ai_studio.db")
                if candidate.exists(): db_path=candidate
            if db_path and db_path.exists():
                tmp=target.with_suffix('.sqlite')
                src=sqlite3.connect(str(db_path)); dst=sqlite3.connect(str(tmp)); src.backup(dst); dst.close(); src.close()
                z.write(tmp,"database.sqlite"); tmp.unlink(missing_ok=True)
            if req.include_evidence:
                ev=self.backup_dir.parent/"evidence"
                if ev.exists():
                    for f in ev.rglob('*'):
                        if f.is_file(): z.write(f,Path("evidence")/f.relative_to(ev))
        blob=target.read_bytes(); mh=_sha(_canon(manifest)); fh=_sha(blob)
        with session_scope() as s:
            row=HumanControlBackupModel(backup_key=req.backup_key,workspace_id=req.workspace_id,status="ready",backup_type="control_center",storage_path=str(target),manifest_json=manifest,manifest_hash=mh,file_hash=fh,size_bytes=len(blob),created_by=req.created_by,metadata_json=req.metadata)
            s.add(row); s.flush(); result=self._dump(row)
        await self.event_bus.publish("human_control.backup.created", result)
        return result

    def list_backups(self) -> list[dict[str,Any]]:
        with session_scope() as s: return [self._dump(x) for x in s.scalars(select(HumanControlBackupModel).order_by(HumanControlBackupModel.created_at.desc())).all()]

    def get_backup(self, backup_id:str) -> dict[str,Any]:
        with session_scope() as s:
            row=s.get(HumanControlBackupModel,backup_id)
            if not row: raise HumanControlNotFound("Backup not found.")
            return self._dump(row)

    async def verify(self, backup_id:str) -> dict[str,Any]:
        with session_scope() as s:
            row=s.get(HumanControlBackupModel,backup_id)
            if not row: raise HumanControlNotFound("Backup not found.")
            path=Path(row.storage_path); ok=path.exists() and _sha(path.read_bytes())==row.file_hash
            if ok:
                with zipfile.ZipFile(path) as z: ok=z.testzip() is None and "manifest.json" in z.namelist()
            row.status="verified" if ok else "failed"; row.verified_at=datetime.now(timezone.utc); row.error="" if ok else "Backup integrity validation failed."
            result={"backup_id":row.id,"valid":ok,"file_hash":row.file_hash,"manifest_hash":row.manifest_hash}
        await self.event_bus.publish("human_control.backup.verified", result)
        return result

    async def restore(self, backup_id:str, req:HumanControlRestoreRequest) -> dict[str,Any]:
        with session_scope() as s:
            existing=s.scalar(select(HumanControlRestoreRunModel).where(HumanControlRestoreRunModel.idempotency_key==req.idempotency_key))
            if existing: return self._restore_dump(existing)
            backup=s.get(HumanControlBackupModel,backup_id)
            if not backup: raise HumanControlNotFound("Backup not found.")
            valid=Path(backup.storage_path).exists() and _sha(Path(backup.storage_path).read_bytes())==backup.file_hash
            run=HumanControlRestoreRunModel(backup_id=backup_id,idempotency_key=req.idempotency_key,status="validated" if req.dry_run and valid else ("completed" if valid and req.force else "failed"),dry_run=req.dry_run,requested_by=req.requested_by,reason=req.reason,validation_json={"valid":valid,"requires_force":not req.dry_run},result_json={"message":"Dry-run completed" if req.dry_run else "Restore package validated; database replacement must occur while application is stopped."},error="" if valid else "Invalid backup",completed_at=datetime.now(timezone.utc))
            s.add(run); s.flush(); result=self._restore_dump(run)
        await self.event_bus.publish("human_control.restore.completed", result)
        return result

    @staticmethod
    def _dump(x):
        return {"id":x.id,"backup_key":x.backup_key,"workspace_id":x.workspace_id,"status":x.status,"backup_type":x.backup_type,"storage_path":x.storage_path,"manifest":x.manifest_json,"manifest_hash":x.manifest_hash,"file_hash":x.file_hash,"size_bytes":x.size_bytes,"created_by":x.created_by,"created_at":x.created_at}
    @staticmethod
    def _restore_dump(x):
        return {"id":x.id,"backup_id":x.backup_id,"status":x.status,"dry_run":x.dry_run,"requested_by":x.requested_by,"validation":x.validation_json,"result":x.result_json,"error":x.error}
