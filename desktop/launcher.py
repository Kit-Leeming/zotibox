"""Keep startup failures inspectable in a windowed executable."""
import sys
import tempfile
import traceback
from pathlib import Path

try:
    if "--zotify-worker" in sys.argv:
        from dj_library.zotify_worker import main
        result = main(sys.argv[sys.argv.index("--zotify-worker") + 1:])
    else:
        from dj_library.application import main
        result = main()
except Exception:
    Path(tempfile.gettempdir(), "ZotiBox-startup.log").write_text(traceback.format_exc(), encoding="utf-8")
    raise
raise SystemExit(result)
