import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backend.database.base import Base
from backend.database import models
from backend.task_engine import models as task_models
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskCreate
from backend.task_engine.service import TaskService
from backend.core.events import EventBus
@pytest.mark.asyncio
async def test_created_event():
    e=create_engine('sqlite+pysqlite:///:memory:',future=True); Base.metadata.create_all(e); got=[]; bus=EventBus()
    async def h(ev): got.append(ev)
    bus.subscribe('task.created',h)
    with Session(e) as s: row=await TaskService(TaskRepository(s),bus).create_task(TaskCreate(title='Test')); s.commit()
    assert row['status']=='created' and len(got)==1
