"""Language adapters for API surface and dead-code checks."""

from architectural_gate.plugins.base import LanguageAPIAdapter, get_api_adapter
from architectural_gate.plugins.go_api import GoAPIAdapter
from architectural_gate.plugins.javascript_api import JavaScriptAPIAdapter
from architectural_gate.plugins.python_api import PythonAPIAdapter
from architectural_gate.plugins.rust_api import RustAPIAdapter

__all__ = [
    "LanguageAPIAdapter",
    "PythonAPIAdapter",
    "JavaScriptAPIAdapter",
    "GoAPIAdapter",
    "RustAPIAdapter",
    "get_api_adapter",
]
