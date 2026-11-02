"""Start the plugin API over an in-memory world, for the plugin's contract test. Prints JSON with port and keys."""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from rmzotero.api import Api  # noqa: E402
from rmzotero.config import Config, Settings  # noqa: E402
from rmzotero.engine import Engine  # noqa: E402
from rmzotero.state import State  # noqa: E402
from rmzotero.tablet.cloud import MemoryCloud  # noqa: E402
from rmzotero.zotero.client import MemoryZotero  # noqa: E402
from rmzotero.zotero.webdav import MemoryFiles  # noqa: E402
from test_engine import Tablet, add_paper, highlight_of_first_word  # noqa: E402

tmp = Path(tempfile.mkdtemp())
z, files, cloud = MemoryZotero(7), MemoryFiles(), MemoryCloud()
ml = z.add_collection("ML")
tr = z.add_collection("Transformers", parent=ml)
course = z.add_collection("Course")
item, att = add_paper(z, files, [tr, course])
z.add_item(highlight_of_first_word(att))
cfg = Config(data_dir=tmp, zotero_user_id=7, zotero_api_key="", webdav_url="", webdav_username="", webdav_password="",
             rmfakecloud_url="", quiet_seconds=0, defaults=Settings(ocr=False))
eng = Engine(cfg, State(tmp / "s.db"), z, files, cloud)
eng.sync()
Tablet(cloud, tmp).open("Vaswani - 2017 - Attention")  # Zotero annotations are drawn after the first open
eng.sync()
server = Api(eng, "secret").serve("127.0.0.1", 0)
print(json.dumps({"port": server.server_address[1], "item": item, "att": att, "ml": ml, "tr": tr, "course": course}), flush=True)
while True:
    time.sleep(3600)
