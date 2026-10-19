# Copyright (c) 2026 Novain Shibu. AGPL-3.0-or-later, or a commercial licence (see COMMERCIAL.md).
"""unshape: exact Unicode text from complex-script PDFs with broken text layers, without OCR."""
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # the research modules import each other by plain name
__version__ = "0.2.1"
