import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from examples.codebase_explorer.__main__ import orchestrator

port = int(os.environ.get("PORT", 5050))
orchestrator.serve(port=port)
