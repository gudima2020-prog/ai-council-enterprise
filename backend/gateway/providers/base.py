from __future__ import annotations

from abc import ABC, abstractmethod

from backend.gateway.schemas import GatewayRequest, GatewayResponse


class ProviderAdapter(ABC):
    name: str

    @abstractmethod
    def complete(self, request: GatewayRequest) -> GatewayResponse:
        raise NotImplementedError
