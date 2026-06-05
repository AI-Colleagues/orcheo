"""Keep Canvas template previews aligned with ingested workflow Mermaid."""

from __future__ import annotations
import re
import sys
import types
from pathlib import Path
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.ingestion import ingest_langgraph_script
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.connectors.listener_base import ListenerNode


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_DIR = _REPO_ROOT / "apps/canvas/src/features/workflow/data/templates"
_ASSET_IMPORT_RE = re.compile(
    r'import\s+\w+\s+from\s+"\.\/assets\/([^\"]+)\/workflow\.py\?raw";'
)
_MERMAID_RE = re.compile(r"const\s+\w+_MERMAID\s*=\s*`(.*?)`;", re.S)


class _WeComListenerPluginNode(ListenerNode):
    platform: str = "wecom"
    bot_id: str = ""
    bot_secret: str = ""


class _LarkListenerPluginNode(ListenerNode):
    platform: str = "lark"
    app_id: str = ""
    app_secret: str = ""


class _WeComWsReplyNode(TaskNode):
    message: str = ""
    raw_event: str = ""
    subscription_id: str = ""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, str | None]:
        del state, config
        return {}


def _template_paths() -> list[Path]:
    """Return template TS paths that have both a Mermaid constant and a workflow.py."""
    result = []
    for path in sorted(_TEMPLATE_DIR.glob("*.ts")):
        text = path.read_text()
        if not _MERMAID_RE.search(text):
            continue
        # Skip templates whose workflow.py has been removed (e.g. after external
        # agent removal).  The corresponding TS assets are retained for the canvas
        # build; the Python side simply has nothing to validate against.
        asset_match = _ASSET_IMPORT_RE.search(text)
        if asset_match is None:
            continue
        script_path = _TEMPLATE_DIR / "assets" / asset_match.group(1) / "workflow.py"
        if not script_path.exists():
            continue
        result.append(path)
    return result


@pytest.fixture()
def plugin_template_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install lightweight plugin stubs so plugin-backed templates can ingest."""

    wecom_module = types.ModuleType("orcheo_plugin_wecom_listener")
    wecom_module.WeComListenerPluginNode = _WeComListenerPluginNode
    wecom_module.WeComWsReplyNode = _WeComWsReplyNode
    lark_module = types.ModuleType("orcheo_plugin_lark_listener")
    lark_module.LarkListenerPluginNode = _LarkListenerPluginNode
    wechat_module = types.ModuleType("orcheo_plugin_wechat_listener")

    class _WechatListenerPluginNode(ListenerNode):
        platform: str = "wechat"
        account_id: str = ""
        bot_token: str = ""
        base_url: str = ""
        bot_identity_key: str = ""

    class _WechatReplyNode(TaskNode):
        account_id: str = ""
        bot_token: str = ""
        base_url: str = ""
        message: str = ""
        reply_target: dict[str, str] | str = {}
        raw_event: dict[str, str] | str = {}

        async def run(
            self, state: State, config: RunnableConfig
        ) -> dict[str, bool | str | None]:
            del state, config
            return {"sent": True}

    wechat_module.WechatListenerPluginNode = _WechatListenerPluginNode
    wechat_module.WechatReplyNode = _WechatReplyNode

    monkeypatch.setitem(sys.modules, "orcheo_plugin_wecom_listener", wecom_module)
    monkeypatch.setitem(sys.modules, "orcheo_plugin_lark_listener", lark_module)
    monkeypatch.setitem(sys.modules, "orcheo_plugin_wechat_listener", wechat_module)


@pytest.mark.parametrize(
    "template_path",
    _template_paths(),
    ids=lambda path: path.name,
)
def test_canvas_template_mermaid_matches_ingested_workflow_preview(
    template_path: Path,
    plugin_template_modules: None,
) -> None:
    """Template cards should render the same Mermaid preview as saved workflows."""

    template_source = template_path.read_text()
    asset_import_match = _ASSET_IMPORT_RE.search(template_source)
    mermaid_match = _MERMAID_RE.search(template_source)

    assert asset_import_match is not None
    assert mermaid_match is not None

    script_path = _TEMPLATE_DIR / "assets" / asset_import_match.group(1) / "workflow.py"
    ingested_mermaid = ingest_langgraph_script(script_path.read_text())["index"].get(
        "mermaid"
    )

    assert ingested_mermaid is not None
    assert mermaid_match.group(1).strip() == ingested_mermaid.strip()
