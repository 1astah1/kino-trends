import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="kt-test-"))
os.environ["KT_VAR"] = str(TMP)
os.environ["KT_DB"] = str(TMP / "test.sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
