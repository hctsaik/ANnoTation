"""Source render checks only; these do not certify browser interaction or Kit UI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

RENDER = r'''
import json, logging, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from streamlit.testing.v1 import AppTest
stage = sys.argv[2]
app = AppTest.from_file(str(Path(sys.argv[1]) / "app.py"), default_timeout=15)
app.session_state["m012_app_stage"] = stage
app.session_state["m012_stage_nav"] = stage
app.session_state["m012_review_confirmed"] = True
app.run()
result = {"stage": stage, "exceptions": [item.message for item in app.exception], "errors": [item.value for item in app.error]}
print(json.dumps(result))
logging.shutdown()
if result["exceptions"] or result["errors"]:
    raise SystemExit(1)
'''


class StreamlitSurface(unittest.TestCase):
    def test_empty_then_existing_local_stages(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            env = {**os.environ, "APP_DATA_DIR": str(workspace / "data"), "CIM_LOG_DIR": str(workspace / "data"), "PYTHONIOENCODING": "utf-8"}

            def run(args):
                result = subprocess.run([sys.executable, *args], env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result

            run(["-c", RENDER, str(ROOT), "data"])
            for phase in ("create", "annotate"):
                run([str(Path(__file__).with_name("test_local_workflow.py")), phase, folder])
            for stage in ("data", "workspace", "review", "export"):
                rendered = run(["-c", RENDER, str(ROOT), stage])
                self.assertEqual(json.loads(rendered.stdout.splitlines()[-1])["stage"], stage)


if __name__ == "__main__":
    unittest.main()
