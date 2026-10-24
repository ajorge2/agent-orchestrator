import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Hosted model tools can inspect only this deliberately public example tree.
if os.environ.get("PORT"):
    os.environ["ORCVIEW_EXPLORER_ROOT"] = str(Path(__file__).parent / "examples" / "codebase_explorer" / "demo")
from examples.codebase_explorer.__main__ import orchestrator

port = int(os.environ.get("PORT", 5050))
orchestrator.serve(port=port)
