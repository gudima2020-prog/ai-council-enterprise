from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterator

from sqlalchemy.orm import Session

from backend.agent_governance.runtime import (
    AgentToolRuntimeGovernance,
)
from backend.autonomy.forecast import MissionForecastService
from backend.autonomy.learning import MissionLearningService
from backend.autonomy.governance import MissionGovernanceService
from backend.autonomy.memory import MissionMemoryService
from backend.autonomy.portfolio import MissionPortfolioService
from backend.autonomy.resources import MissionResourceService
from backend.autonomy.schedule import MissionScheduleService
from backend.autonomy.service import AutonomousMissionService
from backend.autonomy.strategy import MissionStrategyService
from backend.autonomy.workspace_resources import WorkspaceResourceCoordinator
from backend.control_center.service import HumanControlCenterService
from backend.control_center.auth import HumanControlAuthService
from backend.control_center.browser_security import HumanControlBrowserSecurityService
from backend.control_center.governance import HumanControlGovernanceService
from backend.control_center.notifications import HumanControlNotificationService
from backend.control_center.routing import HumanControlRoutingService
from backend.control_center.compliance import HumanControlComplianceService
from backend.control_center.retention import HumanControlRetentionService
from backend.control_center.backup import HumanControlBackupService
from backend.secrets.service import SecretManagerService
from backend.core.config import AppSettings, get_settings
from backend.core.events import EventBus, event_bus
from backend.database.session import session_scope
from backend.plugins.registry import PluginLoader, plugin_loader
from backend.repositories.chats import ChatRepository
from backend.repositories.memory import MemoryRepository
from backend.repositories.models import ModelRepository
from backend.repositories.projects import ProjectRepository
from backend.repositories.settings import SettingsRepository
from backend.repositories.workspaces import WorkspaceRepository
from backend.orchestration.collaboration import AgentCollaborationManager
from backend.orchestration.critic import ExecutionPlanCriticService
from backend.orchestration.agents import (
    AgentAssignmentService,
    AgentRegistryService,
    AgentRepository,
)
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.planner import ExecutionPlannerService
from backend.orchestration.runtime import ExecutionPlanRuntime
from backend.orchestration.tool_runtime import (
    ToolAwareExecutionPlanRuntime,
    ToolExecutionRuntime,
)
from backend.orchestration.tools import (
    ToolRegistryService,
    ToolRepository,
)
from backend.orchestration.service import ExecutionPlanService
from backend.orchestration.supervisor import ExecutionSupervisorService
from backend.orchestration.observability import ExecutionObservabilityService
from backend.orchestration.distributed import (
    DistributedExecutionWorker,
    ExecutionDistributionCoordinator,
)
from backend.orchestration.transport import ExecutionEventTransport
from backend.services.model_manager import ModelManager
from backend.services.workspace_policy import WorkspacePolicyService
from backend.services.workspace_state import ActiveWorkspaceService
from backend.services.workspaces import WorkspaceService
from backend.task_engine.admission import TaskAdmissionManager
from backend.task_engine.dead_letter import TaskDeadLetterManager
from backend.task_engine.approvals import TaskApprovalManager
from backend.task_engine.audit import TaskAuditManager
from backend.task_engine.parallel_executor import ParallelTaskExecutor
from backend.task_engine.queue import TaskQueue
from backend.task_engine.repository import TaskRepository
from backend.task_engine.scheduler import TaskScheduler
from backend.task_engine.service import TaskService
from backend.task_engine.workflow import TaskWorkflowEngine


TASK_WORKER_COUNT = 4


@dataclass
class AppContainer:
    settings: AppSettings
    event_bus: EventBus
    plugin_loader: PluginLoader
    task_queue: TaskQueue | None = None
    task_scheduler: TaskScheduler | None = None
    task_executor: ParallelTaskExecutor | None = None
    task_workflow_engine: TaskWorkflowEngine | None = None
    task_approval_manager: TaskApprovalManager | None = None
    task_audit_manager: TaskAuditManager | None = None
    task_admission_manager: TaskAdmissionManager | None = None
    task_dead_letter_manager: TaskDeadLetterManager | None = None
    execution_plan_runtime: ExecutionPlanRuntime | None = None
    execution_planner_service: ExecutionPlannerService | None = None
    execution_plan_critic_service: ExecutionPlanCriticService | None = None
    execution_supervisor_service: ExecutionSupervisorService | None = None
    execution_observability_service: ExecutionObservabilityService | None = None
    execution_distribution_coordinator: ExecutionDistributionCoordinator | None = None
    execution_distributed_worker: DistributedExecutionWorker | None = None
    execution_event_transport: ExecutionEventTransport | None = None
    tool_execution_runtime: ToolExecutionRuntime | None = None
    agent_collaboration_manager: AgentCollaborationManager | None = None
    autonomous_mission_service: AutonomousMissionService | None = None
    mission_memory_service: MissionMemoryService | None = None
    mission_forecast_service: MissionForecastService | None = None
    mission_learning_service: MissionLearningService | None = None
    mission_governance_service: MissionGovernanceService | None = None
    mission_strategy_service: MissionStrategyService | None = None
    mission_resource_service: MissionResourceService | None = None
    mission_schedule_service: MissionScheduleService | None = None
    mission_portfolio_service: MissionPortfolioService | None = None
    workspace_resource_coordinator: WorkspaceResourceCoordinator | None = None
    human_control_center_service: HumanControlCenterService | None = None
    human_control_governance_service: HumanControlGovernanceService | None = None
    human_control_auth_service: HumanControlAuthService | None = None
    human_control_browser_security_service: HumanControlBrowserSecurityService | None = None
    human_control_notification_service: HumanControlNotificationService | None = None
    human_control_routing_service: HumanControlRoutingService | None = None
    human_control_compliance_service: HumanControlComplianceService | None = None
    human_control_retention_service: HumanControlRetentionService | None = None
    human_control_backup_service: HumanControlBackupService | None = None
    secret_manager_service: SecretManagerService | None = None

    @classmethod
    def build_default(cls) -> "AppContainer":
        return cls(
            settings=get_settings(),
            event_bus=event_bus,
            plugin_loader=plugin_loader,
        )

    async def start_task_runtime(self) -> TaskScheduler:
        if self.task_scheduler is not None and self.task_scheduler.running:
            return self.task_scheduler

        self.task_audit_manager = TaskAuditManager()
        self.task_admission_manager = TaskAdmissionManager(
            event_bus=self.event_bus,
        )
        self.task_queue = TaskQueue()
        self.task_workflow_engine = TaskWorkflowEngine(
            queue=self.task_queue,
            event_bus=self.event_bus,
        )
        self.task_approval_manager = TaskApprovalManager(
            event_bus=self.event_bus,
            workflow_engine=self.task_workflow_engine,
        )
        self.task_dead_letter_manager = TaskDeadLetterManager(
            event_bus=self.event_bus,
            queue=self.task_queue,
            workflow_engine=self.task_workflow_engine,
        )
        self.task_executor = ParallelTaskExecutor(
            queue=self.task_queue,
            event_bus=self.event_bus,
            workflow_engine=self.task_workflow_engine,
            admission_manager=self.task_admission_manager,
        )
        self.task_scheduler = TaskScheduler(
            queue=self.task_queue,
            event_bus=self.event_bus,
            handler=self.task_executor.execute_queue_item,
            worker_count=TASK_WORKER_COUNT,
        )
        self.agent_collaboration_manager = AgentCollaborationManager(
            event_bus=self.event_bus,
        )
        self.secret_manager_service = SecretManagerService(
            event_bus=self.event_bus,
        )
        self.tool_execution_runtime = ToolExecutionRuntime(
            event_bus=self.event_bus,
            secret_manager=self.secret_manager_service,
            agent_governance=AgentToolRuntimeGovernance(
                workspace_policy_resolver=(
                    lambda session, workspace_id: (
                        self.workspace_policy_service(
                            session
                        ).get_effective_policy(
                            workspace_id
                        )
                    )
                ),
            ),
        )
        self.execution_plan_runtime = ToolAwareExecutionPlanRuntime(
            event_bus=self.event_bus,
            tool_runtime=self.tool_execution_runtime,
            collaboration_manager=self.agent_collaboration_manager,
        )
        self.execution_plan_critic_service = ExecutionPlanCriticService(
            event_bus=self.event_bus,
        )
        self.execution_plan_runtime.set_preflight_hook(
            self.execution_plan_critic_service.ensure_approved_for_run
        )
        self.execution_planner_service = ExecutionPlannerService(
            event_bus=self.event_bus,
            runtime=self.execution_plan_runtime,
        )
        self.mission_memory_service = MissionMemoryService(
            event_bus=self.event_bus,
        )
        self.mission_learning_service = MissionLearningService(
            event_bus=self.event_bus,
        )
        self.mission_forecast_service = MissionForecastService(
            event_bus=self.event_bus,
        )
        self.mission_governance_service = MissionGovernanceService(
            event_bus=self.event_bus,
        )
        self.mission_strategy_service = MissionStrategyService(
            event_bus=self.event_bus,
        )
        self.mission_resource_service = MissionResourceService(
            event_bus=self.event_bus,
        )
        self.mission_schedule_service = MissionScheduleService(
            event_bus=self.event_bus,
        )
        self.mission_portfolio_service = MissionPortfolioService(
            event_bus=self.event_bus,
        )
        self.workspace_resource_coordinator = WorkspaceResourceCoordinator(
            event_bus=self.event_bus,
        )
        self.human_control_center_service = HumanControlCenterService(
            event_bus=self.event_bus,
            task_approval_manager=self.task_approval_manager,
            mission_governance_service=self.mission_governance_service,
            mission_resource_service=self.mission_resource_service,
            workspace_resource_coordinator=self.workspace_resource_coordinator,
            mission_learning_service=self.mission_learning_service,
        )
        self.human_control_governance_service = HumanControlGovernanceService(
            event_bus=self.event_bus,
            control_center=self.human_control_center_service,
            escalation_scan_interval_seconds=max(
                5,
                int(
                    os.getenv(
                        "AI_STUDIO_HUMAN_CONTROL_ESCALATION_SCAN_SECONDS",
                        "30",
                    )
                ),
            ),
        )
        self.human_control_auth_service = HumanControlAuthService(
            event_bus=self.event_bus,
            governance=self.human_control_governance_service,
        )
        self.human_control_browser_security_service = HumanControlBrowserSecurityService(
            event_bus=self.event_bus,
            auth_service=self.human_control_auth_service,
        )
        self.human_control_notification_service = HumanControlNotificationService(
            event_bus=self.event_bus,
            control_center=self.human_control_center_service,
            governance=self.human_control_governance_service,
            dispatch_interval_seconds=max(
                2,
                int(
                    os.getenv(
                        "AI_STUDIO_HUMAN_CONTROL_NOTIFICATION_SECONDS",
                        "10",
                    )
                ),
            ),
            delivery_batch_size=max(
                1,
                int(
                    os.getenv(
                        "AI_STUDIO_HUMAN_CONTROL_NOTIFICATION_BATCH",
                        "100",
                    )
                ),
            ),
        )
        self.human_control_routing_service = HumanControlRoutingService(
            event_bus=self.event_bus,
            notification_service=self.human_control_notification_service,
            scan_interval_seconds=max(
                5,
                int(
                    os.getenv(
                        "AI_STUDIO_HUMAN_CONTROL_ROUTING_SECONDS",
                        "15",
                    )
                ),
            ),
        )
        self.human_control_notification_service.set_routing_service(
            self.human_control_routing_service
        )
        self.human_control_compliance_service = HumanControlComplianceService(
            event_bus=self.event_bus,
        )
        self.human_control_retention_service = HumanControlRetentionService(
            event_bus=self.event_bus,
        )
        self.human_control_backup_service = HumanControlBackupService(
            event_bus=self.event_bus,
        )
        self.autonomous_mission_service = AutonomousMissionService(
            event_bus=self.event_bus,
            planner=self.execution_planner_service,
            scheduler_interval_seconds=max(
                5,
                int(os.getenv("AI_STUDIO_MISSION_TICK_SECONDS", "15")),
            ),
            memory_context_provider=self.mission_memory_service.build_context,
            governance_context_provider=(
                self.mission_governance_service.build_context
            ),
            strategy_context_provider=(
                self.mission_strategy_service.build_context
            ),
            cycle_strategy_assigner=(
                self.mission_strategy_service.assign_cycle
            ),
            resource_context_provider=(
                self.mission_resource_service.build_context
            ),
            cycle_resource_allocator=(
                self.mission_resource_service.prepare_cycle
            ),
            cycle_admission_guard=(
                self.mission_governance_service.evaluate_cycle_admission
            ),
            schedule_context_provider=(
                self.mission_schedule_service.build_context
            ),
            cycle_schedule_guard=(
                self.mission_schedule_service.evaluate_cycle_admission
            ),
            schedule_next_resolver=(
                self.mission_schedule_service.next_cycle_at
            ),
            schedule_tick_hook=(
                self.mission_schedule_service.scheduler_tick
            ),
            portfolio_context_provider=(
                self.mission_portfolio_service.build_context
            ),
            cycle_portfolio_guard=(
                self.mission_portfolio_service.evaluate_cycle_admission
            ),
            portfolio_tick_hook=(
                self.mission_portfolio_service.scheduler_tick
            ),
            workspace_resource_context_provider=(
                self.workspace_resource_coordinator.build_context
            ),
            cycle_workspace_resource_allocator=(
                self.workspace_resource_coordinator.prepare_cycle
            ),
            workspace_resource_tick_hook=(
                self.workspace_resource_coordinator.scheduler_tick
            ),
            forecast_context_provider=(
                self.mission_forecast_service.build_context
            ),
            learning_context_provider=(
                self.mission_learning_service.build_context
            ),
        )
        self.execution_supervisor_service = ExecutionSupervisorService(
            event_bus=self.event_bus,
            runtime=self.execution_plan_runtime,
            planner=self.execution_planner_service,
        )
        self.execution_observability_service = ExecutionObservabilityService(
            event_bus=self.event_bus,
        )
        self.execution_distribution_coordinator = (
            ExecutionDistributionCoordinator(event_bus=self.event_bus)
        )
        self.execution_event_transport = ExecutionEventTransport(
            event_bus=self.event_bus,
            node_id=os.getenv("AI_STUDIO_TRANSPORT_NODE_ID") or None,
            reconcile_interval_seconds=max(
                5,
                int(os.getenv("AI_STUDIO_TRANSPORT_RECONCILE_SECONDS", "15")),
            ),
        )

        await self.execution_distribution_coordinator.reconcile()
        await self.execution_event_transport.reconcile()
        if os.getenv("AI_STUDIO_DISTRIBUTED_WORKER_ENABLED", "0").lower() in {
            "1", "true", "yes", "on"
        }:
            self.execution_distributed_worker = DistributedExecutionWorker(
                coordinator=self.execution_distribution_coordinator,
                runtime=self.execution_plan_runtime,
                event_bus=self.event_bus,
                worker_key=os.getenv("AI_STUDIO_WORKER_KEY") or None,
                queues=[
                    item.strip()
                    for item in os.getenv(
                        "AI_STUDIO_WORKER_QUEUES", "default"
                    ).split(",")
                    if item.strip()
                ],
                capabilities=[
                    item.strip()
                    for item in os.getenv(
                        "AI_STUDIO_WORKER_CAPABILITIES",
                        "execution_plan",
                    ).split(",")
                    if item.strip()
                ],
                max_concurrency=max(
                    1,
                    int(os.getenv("AI_STUDIO_WORKER_CONCURRENCY", "1")),
                ),
                lease_seconds=max(
                    5,
                    int(os.getenv("AI_STUDIO_WORKER_LEASE_SECONDS", "90")),
                ),
            )

        await self.autonomous_mission_service.start()
        await self.execution_supervisor_service.start()
        await self.execution_observability_service.start()
        await self.execution_event_transport.start()
        await self.task_scheduler.start()
        await self.task_executor.recover_persistent_queue()
        await self.task_approval_manager.reconcile_expired()
        await self.task_workflow_engine.reconcile_all()
        await self.task_dead_letter_manager.reconcile()
        if self.human_control_governance_service is not None:
            self.human_control_governance_service.seed_builtin_roles()
        if self.secret_manager_service is not None:
            self.secret_manager_service.seed_builtin_providers()
            self.secret_manager_service.seed_access_defaults()
            await self.secret_manager_service.reconcile_leases()
            await self.secret_manager_service.start_lifecycle_monitor()
        if self.human_control_notification_service is not None:
            self.human_control_notification_service.seed_defaults()
        if self.human_control_center_service is not None:
            await self.human_control_center_service.sync()
        if self.human_control_auth_service is not None:
            await self.human_control_auth_service.reconcile()
        if self.human_control_browser_security_service is not None:
            await self.human_control_browser_security_service.reconcile()
        if self.human_control_routing_service is not None:
            await self.human_control_routing_service.reconcile()
            await self.human_control_routing_service.start()
        if self.human_control_notification_service is not None:
            await self.human_control_notification_service.reconcile()
            await self.human_control_notification_service.sync_open_items()
            await self.human_control_notification_service.start()
        if self.human_control_governance_service is not None:
            await self.human_control_governance_service.scan_escalations()
            await self.human_control_governance_service.start()
        protected_plan_ids = (
            self.execution_distribution_coordinator.active_plan_ids()
            if self.execution_distribution_coordinator is not None
            else set()
        )
        await self.execution_plan_runtime.recover_interrupted(
            exclude_plan_ids=protected_plan_ids
        )
        if self.execution_distributed_worker is not None:
            await self.execution_distributed_worker.start()
        return self.task_scheduler

    async def stop_task_runtime(self) -> None:
        executor = self.task_executor
        scheduler = self.task_scheduler
        plan_runtime = self.execution_plan_runtime
        planner_service = self.execution_planner_service
        supervisor_service = self.execution_supervisor_service
        observability_service = self.execution_observability_service
        tool_runtime = self.tool_execution_runtime
        distributed_worker = self.execution_distributed_worker
        event_transport = self.execution_event_transport
        mission_service = self.autonomous_mission_service
        human_control_governance = self.human_control_governance_service
        human_control_notifications = self.human_control_notification_service
        human_control_routing = self.human_control_routing_service
        secret_manager = self.secret_manager_service

        if secret_manager is not None:
            await secret_manager.shutdown_lifecycle_monitor()
        if human_control_routing is not None:
            await human_control_routing.shutdown()
        if human_control_notifications is not None:
            await human_control_notifications.shutdown()
        if human_control_governance is not None:
            await human_control_governance.shutdown()
        if distributed_worker is not None:
            await distributed_worker.shutdown()
        if mission_service is not None:
            await mission_service.shutdown()
        if observability_service is not None:
            await observability_service.shutdown()
        if supervisor_service is not None:
            await supervisor_service.shutdown()
        if planner_service is not None:
            await planner_service.shutdown()
        if plan_runtime is not None:
            await plan_runtime.shutdown()
        if tool_runtime is not None:
            await tool_runtime.shutdown()
        if event_transport is not None:
            await event_transport.shutdown()
        if executor is not None:
            await executor.shutdown()
        if scheduler is not None:
            await scheduler.stop()

        self.secret_manager_service = None
        self.human_control_retention_service = None
        self.human_control_backup_service = None
        self.human_control_compliance_service = None
        self.human_control_routing_service = None
        self.human_control_notification_service = None
        self.human_control_browser_security_service = None
        self.human_control_auth_service = None
        self.human_control_governance_service = None
        self.human_control_center_service = None
        self.workspace_resource_coordinator = None
        self.mission_portfolio_service = None
        self.mission_schedule_service = None
        self.mission_resource_service = None
        self.mission_strategy_service = None
        self.mission_governance_service = None
        self.mission_forecast_service = None
        self.mission_learning_service = None
        self.mission_memory_service = None
        self.autonomous_mission_service = None
        self.execution_distributed_worker = None
        self.execution_distribution_coordinator = None
        self.execution_event_transport = None
        self.execution_observability_service = None
        self.execution_supervisor_service = None
        self.execution_planner_service = None
        self.execution_plan_critic_service = None
        self.execution_plan_runtime = None
        self.tool_execution_runtime = None
        self.agent_collaboration_manager = None
        self.task_dead_letter_manager = None
        self.task_admission_manager = None
        self.task_approval_manager = None
        self.task_audit_manager = None
        self.task_workflow_engine = None
        self.task_scheduler = None
        self.task_executor = None
        self.task_queue = None

    def session(self) -> Iterator[Session]:
        with session_scope() as session:
            yield session

    def model_manager(self, session: Session) -> ModelManager:
        return ModelManager(
            ModelRepository(session),
            self.event_bus,
            self.settings,
        )

    def workspace_service(self, session: Session) -> WorkspaceService:
        return WorkspaceService(
            repository=WorkspaceRepository(session),
            event_bus=self.event_bus,
        )

    def workspace_policy_service(
        self,
        session: Session,
    ) -> WorkspacePolicyService:
        return WorkspacePolicyService(
            workspace_repository=WorkspaceRepository(session),
            settings_repository=SettingsRepository(session),
            model_repository=ModelRepository(session),
            event_bus=self.event_bus,
            app_settings=self.settings,
        )

    def active_workspace_service(
        self,
        session: Session,
    ) -> ActiveWorkspaceService:
        return ActiveWorkspaceService(
            workspace_repository=WorkspaceRepository(session),
            settings_repository=SettingsRepository(session),
            project_repository=ProjectRepository(session),
            chat_repository=ChatRepository(session),
            memory_repository=MemoryRepository(session),
            event_bus=self.event_bus,
        )

    def task_service(self, session: Session) -> TaskService:
        return TaskService(
            TaskRepository(session),
            self.event_bus,
        )

    def execution_plan_service(
        self,
        session: Session,
    ) -> ExecutionPlanService:
        return ExecutionPlanService(
            ExecutionPlanRepository(session),
            self.event_bus,
        )

    def agent_registry_service(
        self,
        session: Session,
    ) -> AgentRegistryService:
        return AgentRegistryService(
            AgentRepository(session),
            self.event_bus,
        )

    def agent_assignment_service(
        self,
        session: Session,
    ) -> AgentAssignmentService:
        return AgentAssignmentService(
            AgentRepository(session),
            ExecutionPlanRepository(session),
            self.event_bus,
        )


    def tool_registry_service(
        self,
        session: Session,
    ) -> ToolRegistryService:
        return ToolRegistryService(
            ToolRepository(session),
            self.event_bus,
        )
