"""The API's whole surface, for the tests that hold every part of it.

models.py and web/src/api.ts were split one module per area (#2168), with no
facade over the modules. A test of every model, or of every interface the
browser declares, reads them all through here.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path
from types import ModuleType
from typing import Any

import pydantic

from uqf_frontend import models as _package
from uqf_frontend import routes as _routes

#: Every module under web/src/api/, read as text (test_api_types.py).
API_DIR = Path(__file__).resolve().parents[3] / "web" / "src" / "api"


def _modules(package: ModuleType) -> list[ModuleType]:
    return [
        importlib.import_module(f"{package.__name__}.{m.name}")
        for m in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name)
    ]


def model_modules() -> list[ModuleType]:
    """Every module under uqf_frontend/models/."""
    return _modules(_package)


def route_modules() -> list[ModuleType]:
    """Every module under uqf_frontend/routes/."""
    return _modules(_routes)


def defined_models() -> dict[str, type[pydantic.BaseModel]]:
    """Every pydantic model the models modules define, by name."""
    found: dict[str, type[pydantic.BaseModel]] = {}
    for module in model_modules():
        for name, obj in vars(module).items():
            if (
                isinstance(obj, type)
                and issubclass(obj, pydantic.BaseModel)
                and obj.__module__ == module.__name__
            ):
                assert name not in found, f"{name} is defined twice"
                found[name] = obj
    return found


def _defines(module: ModuleType) -> set[str]:
    """The names `module` itself binds at its top level - not the ones it imports."""
    tree = ast.parse(Path(module.__file__ or "").read_text())
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def model_named(name: str) -> Any:
    """The model, Literal or constant `name`, from the module defining it."""
    for module in model_modules():
        if name in _defines(module):
            return vars(module)[name]
    raise LookupError(f"no models module defines {name}")


def api_text() -> str:
    """Every web/src/api/ module's text, in name order."""
    return "\n".join(p.read_text() for p in sorted(API_DIR.glob("*.ts")))
