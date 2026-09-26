"""The image must carry every local module the API imports.

The Dockerfile copies the application file by file. A new module imported
by api.py but not copied passed every unit test (they import from the
source tree) and then crashed the container on boot: v2.13.5 shipped
without wizard_adopt.py. This compares the two lists.
"""

import ast
import pathlib
import re

MODULE_DIR = pathlib.Path(__file__).resolve().parents[1]


def local_imports(path: pathlib.Path) -> set[str]:
    local = {p.stem for p in MODULE_DIR.glob("*.py")}
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names & local


def copied_modules() -> set[str]:
    text = (MODULE_DIR / "Dockerfile").read_text()
    return {m.group(1) for m in re.finditer(r"^COPY\s+(\w+)\.py\s", text, re.M)}


def test_every_module_the_app_imports_is_in_the_image():
    needed: set[str] = set()
    todo = ["api", "wsgi"]
    while todo:
        name = todo.pop()
        if name in needed:
            continue
        needed.add(name)
        todo += sorted(local_imports(MODULE_DIR / f"{name}.py") - needed)
    missing = sorted(needed - copied_modules())
    assert not missing, f"imported by the app but not COPY'd in the Dockerfile: {missing}"
