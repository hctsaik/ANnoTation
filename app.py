"""Run with: python -m streamlit run app.py"""
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from annotation_runtime import data_root

os.environ["ANNOTATION_STANDALONE"] = "1"
os.environ["CIM_LOG_DIR"] = str(data_root())
runpy.run_path(str(ROOT / "modules" / "module_012" / "012_runner.py"), run_name="__main__")
