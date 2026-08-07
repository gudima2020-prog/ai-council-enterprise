@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo AI Studio Enterprise - P3-001.5b document AI verification
echo ============================================================

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv not found. Run install.bat first.
  exit /b 1
)

call .venv\Scripts\activate.bat
if errorlevel 1 exit /b 1

set "OCR_IMAGE=%AI_STUDIO_OCR_IMAGE%"
if not defined OCR_IMAGE set "OCR_IMAGE=ai-studio-ocr-tesseract:5-v1"

where git >nul 2>nul
if errorlevel 1 (
  echo ERROR: Git is required.
  exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
  echo ERROR: Node.js is required for the frontend build.
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo ERROR: npm is required for the frontend build.
  exit /b 1
)

echo.
echo Working tree whitespace check:
git diff --check
if errorlevel 1 exit /b 1
git diff --cached --check
if errorlevel 1 exit /b 1

echo.
echo Python compile check:
python -m py_compile ^
  backend\documents\ai_analysis.py ^
  backend\documents\ai_context.py ^
  backend\documents\ai_repository.py ^
  backend\documents\ai_resolver.py ^
  backend\documents\ai_service.py ^
  backend\documents\models.py ^
  backend\gateway\policy.py ^
  backend\gateway\service.py ^
  backend\routers\document_ai.py
if errorlevel 1 exit /b 1

echo.
echo Database migration head check:
python -m alembic heads | findstr /C:"20260806_0057"
if errorlevel 1 (
  echo ERROR: expected Alembic head 20260806_0057.
  exit /b 1
)

echo.
echo P3-001.5b document AI contract check:
python -c "from backend.control_center.governance_schemas import HumanControlPermission; from backend.documents import DocumentAIResponseContract, DocumentAIWorkflow; from backend.gateway.schemas import GatewayRequest; assert DocumentAIWorkflow.QUESTION.value == 'question'; assert HumanControlPermission.DOCUMENT_AI.value == 'document.ai'; assert 'data_classification' in GatewayRequest.__dataclass_fields__; assert DocumentAIResponseContract.MAX_CITATIONS == 2000"
if errorlevel 1 exit /b 1

findstr /C:"P3-001.5b" "docs\P3_001_DOCUMENTS_WORKSPACE.md" >nul
if errorlevel 1 (
  echo ERROR: P3-001.5b documentation marker is missing.
  exit /b 1
)

echo.
echo Documents and Gateway targeted regression:
python -m pytest -q ^
  tests\test_document_intake_core.py ^
  tests\test_document_registry_storage.py ^
  tests\test_document_api.py ^
  tests\test_document_extraction_core.py ^
  tests\test_document_extraction_persistence.py ^
  tests\test_document_extraction_api.py ^
  tests\test_document_ocr_core.py ^
  tests\test_document_ocr_persistence.py ^
  tests\test_document_ocr_api.py ^
  tests\test_document_ai_context_core.py ^
  tests\test_document_ai_analysis_core.py ^
  tests\test_document_ai_resolver.py ^
  tests\test_document_ai_persistence.py ^
  tests\test_document_ai_service.py ^
  tests\test_document_ai_api.py ^
  tests\test_gateway_document_classification.py ^
  tests\test_gateway_policy_enforcement.py ^
  tests\test_gateway_approval_enforcement.py ^
  tests\test_gateway_approval_coordinator.py ^
  tests\test_migration_manager.py ^
  tests\test_openapi_schema.py
if errorlevel 1 exit /b 1

echo.
echo Full backend regression:
python -m pytest -q
if errorlevel 1 exit /b 1

echo.
echo Frontend production build:
pushd frontend
call npm run build
set BUILD_RC=%ERRORLEVEL%
popd
if not "%BUILD_RC%"=="0" exit /b %BUILD_RC%

echo.
echo OCR Docker runtime preflight:
where docker >nul 2>nul
if errorlevel 1 (
  echo INFO: Docker CLI is not installed. Run the 4a preparation script on the target host.
) else (
  docker version >nul 2>nul
  if errorlevel 1 (
    echo INFO: Docker CLI found but daemon is not running.
  ) else (
    docker image inspect "%OCR_IMAGE%" >nul 2>nul
    if errorlevel 1 (
      echo INFO: OCR image is missing. Run prepare_p3_001_4a_ocr_runtime.bat.
    ) else (
      python -c "from backend.documents import DockerTesseractOCRRuntime; status = DockerTesseractOCRRuntime().status(); assert status.enabled and status.image_trusted and status.image_id"
      if errorlevel 1 exit /b 1
      echo OCR runtime image: TRUSTED
    )
  )
)

echo.
echo P3-001.5b verification PASSED.
exit /b 0
