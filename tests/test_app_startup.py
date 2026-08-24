import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_importing_app_does_not_import_heavy_document_modules():
    code = (
        "import app, sys; "
        "assert 'core.retriever' not in sys.modules; "
        "assert 'core.loader' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
