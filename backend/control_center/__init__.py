"""Human approvals, operator command center and governance controls.

The package deliberately exposes its public classes lazily.  Importing a
submodule such as ``backend.control_center.service`` must not import the route
security layer, because that layer depends on ``AppContainer`` and would create
an import cycle while the container itself is being initialised.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.control_center.auth import HumanControlAuthService
    from backend.control_center.browser_security import HumanControlBrowserSecurityService
    from backend.control_center.governance import HumanControlGovernanceService
    from backend.control_center.notifications import HumanControlNotificationService
    from backend.control_center.routing import HumanControlRoutingService
    from backend.control_center.compliance import HumanControlComplianceService
    from backend.control_center.retention import HumanControlRetentionService
    from backend.control_center.governance_schemas import (
        HumanControlApprovalCaseStatus,
        HumanControlApprovalVoteDecision,
        HumanControlPermission,
    )
    from backend.control_center.schemas import (
        HumanControlDecisionAction,
        HumanControlItemStatus,
        HumanControlRiskLevel,
        HumanControlSourceType,
    )
    from backend.control_center.security import (
        HumanControlAuthMiddleware,
        HumanControlPrincipal,
    )
    from backend.control_center.service import HumanControlCenterService


_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "HumanControlCenterService": (
        "backend.control_center.service",
        "HumanControlCenterService",
    ),
    "HumanControlAuthService": (
        "backend.control_center.auth",
        "HumanControlAuthService",
    ),
    "HumanControlBrowserSecurityService": (
        "backend.control_center.browser_security",
        "HumanControlBrowserSecurityService",
    ),
    "HumanControlAuthMiddleware": (
        "backend.control_center.security",
        "HumanControlAuthMiddleware",
    ),
    "HumanControlPrincipal": (
        "backend.control_center.security",
        "HumanControlPrincipal",
    ),
    "HumanControlGovernanceService": (
        "backend.control_center.governance",
        "HumanControlGovernanceService",
    ),
    "HumanControlNotificationService": (
        "backend.control_center.notifications",
        "HumanControlNotificationService",
    ),
    "HumanControlRoutingService": (
        "backend.control_center.routing",
        "HumanControlRoutingService",
    ),
    "HumanControlComplianceService": (
        "backend.control_center.compliance",
        "HumanControlComplianceService",
    ),
    "HumanControlRetentionService": (
        "backend.control_center.retention",
        "HumanControlRetentionService",
    ),
    "HumanControlPermission": (
        "backend.control_center.governance_schemas",
        "HumanControlPermission",
    ),
    "HumanControlApprovalCaseStatus": (
        "backend.control_center.governance_schemas",
        "HumanControlApprovalCaseStatus",
    ),
    "HumanControlApprovalVoteDecision": (
        "backend.control_center.governance_schemas",
        "HumanControlApprovalVoteDecision",
    ),
    "HumanControlDecisionAction": (
        "backend.control_center.schemas",
        "HumanControlDecisionAction",
    ),
    "HumanControlItemStatus": (
        "backend.control_center.schemas",
        "HumanControlItemStatus",
    ),
    "HumanControlRiskLevel": (
        "backend.control_center.schemas",
        "HumanControlRiskLevel",
    ),
    "HumanControlSourceType": (
        "backend.control_center.schemas",
        "HumanControlSourceType",
    ),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve public package exports only when the caller asks for them."""

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
