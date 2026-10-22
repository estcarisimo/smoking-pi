"""Every image whose code classifies an AS also carries the roles file.

The Dockerfiles copy shared/modules/common file by file (`*.py`), so a new
data file reaches no image unless each one names it, and every unit test
still passes from the source tree: the classifier would silently fall back
to KNOWN alone. This compares the code with the Dockerfiles.
"""

import re
from pathlib import Path

MODULES = Path(__file__).resolve().parents[2]
# Modules whose code is baked into another module's image.
IMAGE_OF = {"smokeping-exporters": "smokeping"}
USES = re.compile(r"^\s*from common(\.| import )(asn|operators)\b", re.M)


def users() -> set[str]:
    return {py.parent.name for py in MODULES.glob("*/*.py")
            if py.parent.name != "common" and USES.search(py.read_text(encoding="utf-8"))}


def test_the_users_are_found():
    assert {"inference", "pathmeter", "smokeping-exporters"} <= users()


def test_each_users_image_copies_common_and_its_data():
    for module in sorted(users()):
        dockerfile = MODULES / IMAGE_OF.get(module, module) / "Dockerfile"
        text = dockerfile.read_text(encoding="utf-8")
        assert re.search(r"^COPY modules/common/\*\.py ", text, re.M), dockerfile
        assert re.search(r"^COPY modules/common/data/ ", text, re.M), dockerfile
