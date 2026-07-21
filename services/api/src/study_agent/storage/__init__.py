"""Object-storage adapters."""

from study_agent.storage.local import LocalStorage, StorageBoundaryError

__all__ = ["LocalStorage", "StorageBoundaryError"]
