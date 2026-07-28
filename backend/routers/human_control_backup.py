from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from backend.api.dependencies import get_container
from backend.core.container import AppContainer
from backend.control_center.backup import HumanControlBackupService
from backend.control_center.backup_schemas import HumanControlBackupCreate, HumanControlRestoreRequest
from backend.control_center.service import HumanControlError, HumanControlNotFound, HumanControlConflict
router=APIRouter(tags=["human-control-backup"])
def svc(c:AppContainer=Depends(get_container))->HumanControlBackupService:
    if c.human_control_backup_service is None: raise HTTPException(503,"Backup service not started.")
    return c.human_control_backup_service
@router.get('/human-control/backups/status')
def status(s=Depends(svc)): return s.status()
@router.post('/human-control/backups')
async def create(req:HumanControlBackupCreate,s=Depends(svc)):
    try:return await s.create_backup(req)
    except HumanControlConflict as e: raise HTTPException(409,str(e))
@router.get('/human-control/backups')
def list_(s=Depends(svc)): return {'backups':s.list_backups()}
@router.get('/human-control/backups/{backup_id}')
def get(backup_id:str,s=Depends(svc)):
    try:return s.get_backup(backup_id)
    except HumanControlNotFound as e: raise HTTPException(404,str(e))
@router.post('/human-control/backups/{backup_id}/verify')
async def verify(backup_id:str,s=Depends(svc)):
    try:return await s.verify(backup_id)
    except HumanControlNotFound as e: raise HTTPException(404,str(e))
@router.post('/human-control/backups/{backup_id}/restore')
async def restore(backup_id:str,req:HumanControlRestoreRequest,s=Depends(svc)):
    try:return await s.restore(backup_id,req)
    except HumanControlNotFound as e: raise HTTPException(404,str(e))
