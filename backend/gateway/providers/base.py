from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from backend.gateway.schemas import GatewayRequest, GatewayResponse


class ProviderStreamCancelled(RuntimeError):
    """Raised when a streaming provider request is cancelled by the caller."""


class ProviderAdapter(ABC):
    name: str

    @abstractmethod
    def complete(self, request: GatewayRequest) -> GatewayResponse:
        raise NotImplementedError

    def complete_stream(
        self,
        request: GatewayRequest,
        on_delta: Callable[[str], None],
        is_cancelled: Callable[[], bool] | None = None,
    ) -> GatewayResponse:
        if is_cancelled is not None and is_cancelled():
            raise ProviderStreamCancelled("Provider stream was cancelled.")
        response = self.complete(request)
        if response.status == "success" and response.content:
            on_delta(response.content)
        if is_cancelled is not None and is_cancelled():
            raise ProviderStreamCancelled("Provider stream was cancelled.")
        return response
