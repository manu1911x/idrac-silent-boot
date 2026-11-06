from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseDevice(ABC):
    def __init__(self, name: str, config: Dict[str, Any], general_config: Dict[str, Any], controller: Optional[Any] = None):
        self.name = name
        self.config = config
        self.general_config = general_config
        self.controller = controller

    @abstractmethod
    def on_boot(self) -> None:
        pass

    @abstractmethod
    def poll(self) -> None:
        pass

    @abstractmethod
    def cleanup(self) -> None:
        pass

    def power_on_device(self) -> bool:
        return True

    def power_off_device(self) -> bool:
        return True

    def get_status(self) -> Dict[str, Any]:
        return {"name": self.name, "enabled": True}
