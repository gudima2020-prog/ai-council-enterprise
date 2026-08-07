@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo AI Studio Enterprise - P3-001.5a context core verification
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
  backend\documents\ai_context.py ^
  backend\documents\__init__.py
if errorlevel 1 exit /b 1

echo.
echo Database migration head compatibility check:
python -m alembic heads | findstr /C:"20260806_0057"
if errorlevel 1 (
  echo ERROR: expected compatible Alembic head 20260806_0057.
  exit /b 1
)

echo.
echo P3-001.5a context contract check:
python -c "from backend.documents import ConservativeTokenEstimator, DeterministicDocumentAIContextBuilder, DocumentAIContextPolicy; assert DeterministicDocumentAIContextBuilder.SCHEMA_VERSION == 'p3-001.5a-v1'; assert ConservativeTokenEstimator.ESTIMATOR_VERSION == 'utf8-byte-conservative-v1'; assert DocumentAIContextPolicy().injection_action.value == 'warn'"
if errorlevel 1 exit /b 1

findstr /C:"P3-001.5a" "docs\P3_001_DOCUMENTS_WORKSPACE.md" >nul
if errorlevel 1 (
  echo ERROR: P3-001.5a documentation marker is missing.
  exit /b 1
)

findstr /C:"P3-002" "docs\ROADMAP.md" >nul
if errorlevel 1 (
  echo ERROR: governed agent roadmap marker is missing.
  exit /b 1
)

findstr /C:"P3-003" "docs\ROADMAP.md" >nul
if errorlevel 1 (
  echo ERROR: local utilities roadmap marker is missing.
  exit /b 1
)

echo.
echo Documents Workspace targeted regression:
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
echo P3-001.5a verification PASSED.
exit /b 0
