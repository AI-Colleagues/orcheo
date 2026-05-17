"""FastAPI application entrypoint for the Orcheo backend service."""

from __future__ import annotations
from orcheo_backend.app._app_module import install_app_module_proxy
from orcheo_backend.app._core_exports import *  # noqa: F401,F403
from orcheo_backend.app._core_exports import __all__ as _core_all
from orcheo_backend.app._core_exports import app as app
from orcheo_backend.app._router_exports import *  # noqa: F401,F403
from orcheo_backend.app._router_exports import __all__ as _router_all


__all__: list[str] = list(_core_all)
__all__ += list(_router_all)

install_app_module_proxy(__name__)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    from orcheo.config import get_settings

    uvicorn.run(app, host="0.0.0.0", port=get_settings().port)
