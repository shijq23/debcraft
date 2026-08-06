"""Abstract contracts defining interfaces between platform components.

This package re-exports all abstract base classes (ABCs) and core types for
the platform kernel. Consumer code should import from this package rather
than individual contract modules.
"""

from debcraft.platform.contracts.configuration import ConfigurationService
from debcraft.platform.contracts.container import Container, Scope
from debcraft.platform.contracts.events import DomainEvent, EventBus, EventHandler
from debcraft.platform.contracts.logging import Logger, LoggerFactory
from debcraft.platform.contracts.persistence import DatabaseName, DatabaseProvider, Repository, UnitOfWork
from debcraft.platform.contracts.policies import ExecutionPolicy
from debcraft.platform.contracts.resources import ResourceManager
from debcraft.platform.contracts.storage import StorageEngine, StorageProvider, StoragePurpose
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    ProgressReporter,
    Workflow,
    WorkflowContext,
    WorkflowEngine,
    WorkflowFactory,
    WorkflowState,
    WorkflowSummary,
)

__all__ = [
    "CancellationToken",
    "ConfigurationService",
    "Container",
    "DatabaseName",
    "DatabaseProvider",
    "DomainEvent",
    "EventBus",
    "EventHandler",
    "ExecutionPolicy",
    "Logger",
    "LoggerFactory",
    "ProgressReporter",
    "Repository",
    "ResourceManager",
    "Scope",
    "StorageEngine",
    "StorageProvider",
    "StoragePurpose",
    "UnitOfWork",
    "Workflow",
    "WorkflowContext",
    "WorkflowEngine",
    "WorkflowFactory",
    "WorkflowState",
    "WorkflowSummary",
]
