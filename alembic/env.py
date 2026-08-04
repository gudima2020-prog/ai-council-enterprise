from logging.config import fileConfig
from pathlib import Path
import os
import sys
from alembic import context
from sqlalchemy import engine_from_config,pool
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from backend.core.config import get_settings
from backend.database.base import Base
from backend.database import models
from backend.task_engine import models as task_models
from backend.orchestration import models as orchestration_models
from backend.autonomy import models as autonomy_models
from backend.control_center import models as control_center_models
from backend.secrets import models as secret_models
from backend.council import models as council_models
from backend.code_sandbox import models as code_sandbox_models
from backend.policy_approvals import models as policy_approval_models
config=context.config
if config.config_file_name:fileConfig(config.config_file_name)
target_metadata=Base.metadata
def url():
    # Keep Alembic CLI aligned with backend.database.session. This matters for
    # verification and maintenance commands that intentionally target an isolated DB.
    configured_path = os.getenv('AI_STUDIO_DATABASE_PATH', '').strip()
    if configured_path:
        path = Path(configured_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path.as_posix()}"
    s=get_settings()
    for a in ('database_url','sqlalchemy_database_url','db_url'):
        v=getattr(s,a,None)
        if v:return str(v)
    return config.get_main_option('sqlalchemy.url')
config.set_main_option('sqlalchemy.url',url())
def offline():
    u=config.get_main_option('sqlalchemy.url'); context.configure(url=u,target_metadata=target_metadata,literal_binds=True,dialect_opts={'paramstyle':'named'},compare_type=True,compare_server_default=True,render_as_batch=u.startswith('sqlite'))
    with context.begin_transaction():context.run_migrations()
def online():
    e=engine_from_config(config.get_section(config.config_ini_section) or {},prefix='sqlalchemy.',poolclass=pool.NullPool,future=True)
    with e.connect() as c:
        context.configure(connection=c,target_metadata=target_metadata,compare_type=True,compare_server_default=True,render_as_batch=c.dialect.name=='sqlite')
        with context.begin_transaction():context.run_migrations()
offline() if context.is_offline_mode() else online()
