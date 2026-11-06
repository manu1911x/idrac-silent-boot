from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseDevice(ABC):
    def __init__(self, name: str, config: Dict[str, Any], general_config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.general_config = general_config

    @abstractmethod
    def on_boot(self) -> None:
        pass

    @abstractmethod
    def poll(self) -> None:
        pass

    @abstractmethod
    def cleanup(self) -> None:
        pass
