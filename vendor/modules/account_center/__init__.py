"""Account management contracts. No live adapter or application route is registered yet."""
from .contracts import AccountScope, AdapterUnavailable, get_adapter, platform_catalog

__all__ = ['AccountScope', 'AdapterUnavailable', 'get_adapter', 'platform_catalog']
