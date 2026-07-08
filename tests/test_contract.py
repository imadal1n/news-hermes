from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TypeAlias

from news_hermes.json_types import JsonObject, JsonValue, parse_json_object
from news_hermes.plugin import register
from news_hermes.schemas import TOOL_SCHEMAS
from news_hermes.tools import news_list

ToolHandler: TypeAlias = Callable[..., str]

PACKAGE = Path(__file__).resolve().parents[1] / "news_hermes"
ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = [
    "news_list",
    "news_bookmark",
    "news_dismiss",
    "news_pull",
    "news_clear",
    "news_source_add",
    "news_source_remove",
    "news_source_list",
]
EXPECTED_LAYOUT = [
    "__init__.py",
    "clients.py",
    "config.py",
    "feeds.py",
    "json_types.py",
    "models.py",
    "plugin.py",
    "plugin.yaml",
    "py.typed",
    "schemas.py",
    "storage.py",
    "tools.py",
]


def test_manifest_declares_exact_news_tools() -> None:
    # Given: the Hermes plugin manifest.
    manifest = (PACKAGE / "plugin.yaml").read_text(encoding="utf-8")

    # When: the advertised tool names are inspected.
    advertised = [
        line.strip()[2:] for line in manifest.splitlines() if line.strip().startswith("- ")
    ]

    # Then: only the requested tool names are present.
    assert "name: news-hermes" in manifest
    assert advertised == EXPECTED_TOOLS


def test_register_registers_exact_news_tools() -> None:
    # Given: a Hermes-like host that records registered tools.
    class Host:
        def __init__(self) -> None:
            self.names: list[str] = []

        def register_tool(
            self,
            *,
            name: str,
            toolset: str,
            schema: JsonObject,
            handler: ToolHandler,
        ) -> None:
            self.names.append(name)
            assert toolset == "news-hermes"
            assert schema["name"] == name
            assert callable(handler)

    host = Host()

    # When: the plugin registers itself.
    register(host)

    # Then: Hermes receives exactly the requested tools.
    assert host.names == EXPECTED_TOOLS


def test_tool_handlers_return_json_strings(tmp_path: Path) -> None:
    # Given: a handler called with a temporary storage path.
    news_path = tmp_path / "news.json"

    # When: it lists an empty store.
    result = news_list({}, _news_path=str(news_path))

    # Then: the result is a JSON string.
    payload = parse_json_object(result)
    assert isinstance(result, str)
    assert payload is not None
    assert payload["ok"] is True
    assert payload["items"] == []


def test_installed_directory_plugin_layout_imports(tmp_path: Path) -> None:
    # Given: package files copied into a flat Hermes directory-plugin layout.
    plugin_dir = tmp_path / "news-hermes"
    plugin_dir.mkdir()
    for name in EXPECTED_LAYOUT:
        _ = (plugin_dir / name).write_text((PACKAGE / name).read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location(
        "news_hermes_installed_test",
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module

    # When: the directory plugin is imported through its file location.
    spec.loader.exec_module(module)

    # Then: Hermes-visible entry points are available.
    assert isinstance(module, ModuleType)
    assert "register" in dir(module)
    assert "news_pull" in dir(module)
    assert "news_source_add" in dir(module)


def test_public_files_do_not_leak_local_operational_data() -> None:
    # Given: files intended for a public repository.
    public_files = [ROOT / "README.md", ROOT / "pyproject.toml", PACKAGE / "plugin.yaml"]

    # When: they are scanned for local-only operational markers.
    combined = "\n".join(path.read_text(encoding="utf-8") for path in public_files)

    # Then: only generic public/plugin data is present.
    assert "LOCAL_HOME_MARKER" not in combined
    assert "LOCAL_AGENT_MARKER" not in combined
    assert "LOCAL_BRIDGE_MARKER" not in combined
    assert set(TOOL_SCHEMAS) == set(EXPECTED_TOOLS)


def json_object(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value
