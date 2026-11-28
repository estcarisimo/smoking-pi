"""The image carries every module its entrypoints import.

A Dockerfile that copies files one by one hid a new module from every test
until the image crash-looped (v2.13.5). This reads the Dockerfile's COPY
lines, follows the imports of every entrypoint (the CMD, the lead's compose
command, the healthcheck) through the package, and fails when one resolves
to a file no COPY puts at the path Python will look for it, or to a
third-party package the image does not install. The image installs exactly
pyproject.toml's dependencies, at the same pins."""

import ast
import re
import sys
import tomllib
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
SHARED = MODULE.parents[1]          # the build context
REPO = SHARED.parent
DOCKERFILE = MODULE / "Dockerfile"
WORKDIR = "/app"
ENTRYPOINTS = ("fleet.agent", "fleet.lead", "fleet.status", "fleet.join", "fleet.leave",
               "fleet.admin")
LOCAL_TOPS = ("fleet", "common")


def installed() -> list[str]:
    """The requirement strings the Dockerfile's `pip install` lines name."""
    out = []
    for line in DOCKERFILE.read_text().splitlines():
        if re.match(r"^RUN pip install ", line):
            out += re.findall(r'"([A-Za-z0-9_.-]+[=<>!~][^"]*)"', line)
    return out


def declared() -> list[str]:
    return tomllib.loads((MODULE / "pyproject.toml").read_text())["project"]["dependencies"]


#: Distribution name -> the top-level module it provides.
IMPORT_NAMES = {"cryptography": "cryptography", "gunicorn": "gunicorn"}


def installed_tops() -> set[str]:
    return {IMPORT_NAMES[re.split(r"[=<>!~\[]", r)[0].lower()] for r in installed()}


def copies() -> list[tuple[Path, str]]:
    """(source directory in the repo, destination in the image)."""
    out = []
    for line in DOCKERFILE.read_text().splitlines():
        m = re.match(r"^COPY\s+(?!--from)(\S+)\s+(\S+)\s*$", line)
        if m:
            src, dest = m.groups()
            dest = dest if dest.startswith("/") else f"{WORKDIR}/{dest.lstrip('./')}"
            out.append((SHARED / src.rstrip("/"), dest.rstrip("/")))
    return out


def image_path(source: Path) -> str | None:
    """Where a repo file lands in the image, if a COPY carries it."""
    if "tests" in source.parts or "__pycache__" in source.parts:
        return None  # shared/.dockerignore
    for src, dest in copies():
        if source == src or src in source.parents:
            return f"{dest}/{source.relative_to(src).as_posix()}"
    return None


def source_of(module: str) -> Path | None:
    top, *rest = module.split(".")
    base = MODULE / "fleet" if top == "fleet" else SHARED / "modules" / "common"
    if top not in LOCAL_TOPS:
        return None
    path = base.joinpath(*rest)
    if path.is_dir():
        return path / "__init__.py"
    return path.with_suffix(".py")


def imports(module: str, path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                stem = f"{base}.{node.module}" if node.module else base
            else:
                stem = node.module
            for a in node.names:
                candidate = f"{stem}.{a.name}"
                found.add(candidate if source_of(candidate) and source_of(candidate).exists()
                          else stem)
    return found


def closure() -> dict[str, Path]:
    seen: dict[str, Path] = {}
    todo = list(ENTRYPOINTS) + ["fleet"]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        path = source_of(name)
        if path is None:
            top = name.split(".")[0]
            assert top in sys.stdlib_module_names or top in installed_tops(), (
                f"{name} is neither the standard library nor installed by the Dockerfile")
            continue
        assert path.exists(), f"{name} is imported but {path} does not exist"
        seen[name] = path
        todo.extend(imports(name, path))
        parent = name.rpartition(".")[0]
        if parent:
            todo.append(parent)
    return seen


def test_every_imported_module_is_copied_where_python_finds_it():
    found = closure()
    assert {"fleet.agent", "fleet.lead", "fleet.status", "fleet.protocol", "fleet.admin",
            "fleet.service", "fleet.server", "fleet.pki", "fleet.registry"} <= set(found)
    for name, path in found.items():
        want = f"{WORKDIR}/{name.replace('.', '/')}"
        want += "/__init__.py" if path.name == "__init__.py" else ".py"
        assert image_path(path) == want, f"{name} ({path}) is not copied to {want}"


def test_the_image_installs_exactly_what_pyproject_declares():
    assert installed() == declared(), "Dockerfile and pyproject.toml disagree"
    for req in declared():
        assert "==" in req, f"{req}: pin it, in both files"


def test_the_protocol_stays_the_standard_library():
    """The contract must stay readable by anything: no third-party import."""
    path = MODULE / "fleet" / "protocol.py"
    for name in imports("fleet.protocol", path):
        top = name.split(".")[0]
        assert top in sys.stdlib_module_names or top == "fleet", name


#: The lead's own modules: the node side never imports them.
LEAD_ONLY = {"fleet.lead", "fleet.admin", "fleet.service", "fleet.server", "fleet.pki",
             "fleet.registry"}
NODE_SIDE = ("fleet.agent", "fleet.join", "fleet.leave", "fleet.status", "fleet.layout",
             "fleet.protocol")


def _module_closure(start: str) -> dict[str, Path]:
    seen: dict[str, Path] = {}
    todo = [start]
    while todo:
        name = todo.pop()
        path = source_of(name)
        if name in seen or path is None or not path.exists():
            continue
        seen[name] = path
        todo.extend(imports(name, path))
    return seen


def test_the_node_side_imports_neither_gunicorn_nor_the_lead():
    """The agent may use cryptography (chain.py, nodekeys.py); gunicorn and
    the lead's modules are the lead's alone, and the lead entrypoint imports
    gunicorn only when it serves."""
    for start in NODE_SIDE:
        found = _module_closure(start)
        assert not set(found) & LEAD_ONLY, (start, set(found) & LEAD_ONLY)
        for name, path in found.items():
            tops = {i.split(".")[0] for i in imports(name, path)}
            assert "gunicorn" not in tops, (start, name)
    lead_tops = {i.split(".")[0] for i in imports("fleet.lead", MODULE / "fleet" / "lead.py")}
    assert "gunicorn" not in lead_tops


def test_the_copies_are_whole_directories():
    for src, _dest in copies():
        assert src.is_dir(), f"COPY {src} names a file: copy the package as a whole"


def test_entrypoints_are_the_ones_the_image_and_compose_run():
    text = DOCKERFILE.read_text()
    assert 'CMD ["python", "-m", "fleet.agent"]' in text
    assert '"fleet.status", "--check"' in text
    compose = (REPO / "editions/pro/docker-compose.yml").read_text()
    assert 'command: ["python", "-m", "fleet.lead"]' in compose
