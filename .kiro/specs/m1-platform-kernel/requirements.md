# Requirements Document

## Introduction

This document defines the requirements for Milestone M1 (Platform Kernel) of the DebCraft platform. M1 builds the core runtime infrastructure that all future milestones depend on. The kernel is domain-agnostic — it contains zero Debian-specific logic. It provides dependency injection, event dispatch, workflow orchestration, configuration management, structured logging, resource lifecycle management, and execution policies. All components integrate through constructor injection, expose abstract interfaces in `platform/contracts/`, and place implementations in `platform/kernel/`.

## Glossary

- **Container**: The dependency injection container responsible for service registration, resolution, and lifecycle management.
- **Service**: A class instance managed by the Container with a defined lifetime (singleton, transient, or scoped).
- **Event_Bus**: The in-process publish/subscribe mechanism for dispatching typed domain events to registered handlers.
- **Domain_Event**: An immutable dataclass representing a completed fact, dispatched through the Event_Bus.
- **Workflow_Engine**: The orchestrator that manages workflow lifecycle, cancellation, progress reporting, and summary generation.
- **Workflow**: A first-class execution unit with a defined lifecycle (Created → Running → Completed/Failed/Cancelled).
- **CancellationToken**: A cooperative cancellation mechanism that workflows check periodically to determine if they should stop.
- **WorkflowContext**: An object providing workflows with access to scoped services, resources, and cancellation state.
- **WorkflowFactory**: A factory responsible for creating Workflow instances with their required dependencies.
- **Configuration_Subsystem**: The component responsible for loading, merging, and validating configuration from TOML files, environment variables, and CLI arguments.
- **Configuration_Object**: An immutable typed dataclass representing a validated configuration section.
- **Logger**: The structured logging component providing human-readable and JSON output with component context and correlation IDs.
- **Resource_Manager**: The component managing lifecycle of shared workflow resources with deterministic cleanup.
- **Execution_Policy**: An immutable dataclass controlling operational behavior (concurrency, retries, timeouts, fail-fast).
- **Correlation_ID**: A unique identifier (UUID) assigned to a workflow execution for tracing events and log entries across operations.
- **Scope**: A bounded lifetime context within the Container that owns scoped service instances and disposes them when the scope ends.

## Requirements

### Requirement 1: Dependency Injection Container

**User Story:** As a platform developer, I want a lightweight dependency injection container with constructor injection, so that components are loosely coupled, testable, and have explicit dependencies.

#### Acceptance Criteria

1. THE Container SHALL support registration of services with singleton lifetime, where a single instance is shared across the entire application.
2. THE Container SHALL support registration of services with transient lifetime, where a new instance is created on each resolution.
3. THE Container SHALL support registration of services with scoped lifetime, where a single instance is shared within a Scope and disposed when the Scope ends.
4. WHEN a service is resolved, THE Container SHALL perform constructor injection by inspecting type annotations on the `__init__` method to determine dependencies.
5. WHEN a service depends on another service that depends back on the first service (directly or transitively), THE Container SHALL detect the circular dependency and raise a descriptive error before instantiation.
6. WHEN a service is resolved by its abstract interface type, THE Container SHALL return the registered implementation instance.
7. THE Container SHALL provide type-safe service retrieval using generic type parameters so that callers receive correctly-typed references without casting.
8. WHEN a Scope is created from the Container, THE Container SHALL return a child scope that inherits singleton registrations and creates its own scoped instances.
9. WHEN a Scope is closed, THE Container SHALL dispose all scoped service instances owned by that Scope.
10. THE Container SHALL raise a descriptive error when resolving a service type that has no registration.

### Requirement 2: Event Bus

**User Story:** As a platform developer, I want an in-process publish/subscribe event bus with typed events, so that components communicate through loosely-coupled domain events without direct dependencies.

#### Acceptance Criteria

1. THE Event_Bus SHALL accept subscriptions for specific Domain_Event types, associating a handler callable with each event type.
2. WHEN a Domain_Event is published, THE Event_Bus SHALL dispatch the event to all handlers registered for that event type.
3. THE Event_Bus SHALL support both synchronous and asynchronous handler callables.
4. WHEN an asynchronous handler is registered, THE Event_Bus SHALL await the handler coroutine during dispatch.
5. WHEN multiple handlers are registered for the same event type, THE Event_Bus SHALL invoke handlers in the order they were registered.
6. IF a handler raises an exception during dispatch, THEN THE Event_Bus SHALL catch the exception, log the failure, and continue dispatching to remaining handlers for that event.
7. THE Event_Bus SHALL propagate the Correlation_ID from the publishing context to all handlers receiving the event.
8. WHEN a handler is unsubscribed, THE Event_Bus SHALL stop dispatching events of that type to the removed handler.
9. THE Domain_Event SHALL be an immutable frozen dataclass with a timestamp, event_type name, and Correlation_ID field.
10. THE Event_Bus SHALL support dispatching events to zero handlers without raising errors.

### Requirement 3: Workflow Engine

**User Story:** As a platform developer, I want a workflow engine that manages execution lifecycle with cancellation and progress reporting, so that long-running operations are observable, cancellable, and produce summary reports.

#### Acceptance Criteria

1. THE Workflow_Engine SHALL manage workflows through a lifecycle of states: Created, Running, Completed, Failed, and Cancelled.
2. WHEN a workflow is started, THE Workflow_Engine SHALL transition the workflow from Created to Running.
3. WHEN a workflow completes its execution successfully, THE Workflow_Engine SHALL transition the workflow from Running to Completed.
4. IF a workflow raises an unhandled exception, THEN THE Workflow_Engine SHALL transition the workflow from Running to Failed and record the exception details.
5. WHEN the CancellationToken is triggered, THE Workflow_Engine SHALL set the token's cancelled state so that the workflow can check it cooperatively and stop.
6. WHEN a workflow observes the CancellationToken is cancelled and stops execution, THE Workflow_Engine SHALL transition the workflow from Running to Cancelled.
7. THE Workflow_Engine SHALL provide a progress reporting interface that workflows use to report completion percentage and status messages.
8. WHEN a workflow reaches a terminal state (Completed, Failed, or Cancelled), THE Workflow_Engine SHALL generate a summary report containing workflow name, start time, end time, final state, and any error details.
9. THE WorkflowFactory SHALL create Workflow instances with a WorkflowContext providing access to scoped services, the CancellationToken, and the progress reporter.
10. WHEN the process receives SIGINT (Ctrl+C), THE Workflow_Engine SHALL trigger the CancellationToken for all running workflows and await graceful shutdown within a configurable timeout.
11. THE Workflow_Engine SHALL publish Domain_Events for workflow state transitions (started, completed, failed, cancelled) through the Event_Bus.

### Requirement 4: Configuration Subsystem

**User Story:** As a platform developer, I want a TOML-based configuration system with layered precedence, so that behavior is configurable through files, environment variables, and CLI arguments with predictable override semantics.

#### Acceptance Criteria

1. THE Configuration_Subsystem SHALL load configuration from TOML files using the standard library `tomllib` module.
2. THE Configuration_Subsystem SHALL apply precedence in the following order (highest to lowest): CLI arguments, environment variables, project-level config file (`.debcraft.toml`), user-level config file (`~/.config/debcraft/config.toml`), built-in defaults.
3. WHEN a project-level configuration file exists in the current working directory, THE Configuration_Subsystem SHALL merge its values over the user-level configuration and defaults.
4. THE Configuration_Subsystem SHALL map environment variables using the prefix `DEBCRAFT_` with double underscores as section separators (e.g., `DEBCRAFT_LOGGING__LEVEL` maps to `[logging] level`).
5. WHEN configuration loading is complete, THE Configuration_Subsystem SHALL produce immutable Configuration_Objects (frozen dataclasses) for each section.
6. THE Configuration_Subsystem SHALL validate all configuration values at startup and raise descriptive errors for invalid values before any workflow execution begins.
7. THE Configuration_Subsystem SHALL support typed configuration sections as frozen dataclasses with default values for all fields.
8. THE Configuration_Subsystem SHALL provide a mechanism for plugins to register their own configuration sections with validation rules.
9. WHILE a workflow is executing, THE Configuration_Subsystem SHALL guarantee that Configuration_Objects remain immutable and do not change.
10. IF a required configuration file contains syntax errors, THEN THE Configuration_Subsystem SHALL raise a descriptive error identifying the file path and error location.

### Requirement 5: Logging Framework

**User Story:** As a platform developer, I want structured logging with human-readable console output and optional JSON format, so that workflow execution is observable and log entries are traceable through correlation IDs.

#### Acceptance Criteria

1. THE Logger SHALL produce human-readable console output in the format: `TIMESTAMP LEVEL COMPONENT MESSAGE`.
2. THE Logger SHALL support log levels: DEBUG, INFO, WARNING, and ERROR.
3. THE Logger SHALL attach a component name to every log entry identifying the source module or service.
4. WHEN a Correlation_ID is active in the current context, THE Logger SHALL include the Correlation_ID in every log entry.
5. WHEN the `--log-format=json` CLI flag is specified, THE Logger SHALL produce JSON-formatted log output with fields for timestamp, level, component, message, and correlation_id.
6. THE Logger SHALL be configurable for log level through CLI arguments (`--log-level`), environment variables (`DEBCRAFT_LOGGING__LEVEL`), and configuration file settings, following the Configuration_Subsystem precedence rules.
7. WHILE Rich progress bars or status displays are active on the console, THE Logger SHALL route log output so that log messages do not disrupt the progress display.
8. THE Logger SHALL support workflow-scoped logging where all log entries within a workflow execution share the same Correlation_ID.
9. THE Logger SHALL use Python's standard `logging` module as the underlying implementation, configured with custom formatters and handlers.

### Requirement 6: Resource Manager

**User Story:** As a platform developer, I want a resource manager that provides deterministic lifecycle management for shared resources, so that HTTP sessions, temporary directories, and task groups are properly cleaned up on success, failure, or cancellation.

#### Acceptance Criteria

1. THE Resource_Manager SHALL manage resources that implement the async context manager protocol (`__aenter__` / `__aexit__`).
2. THE Resource_Manager SHALL manage resources that implement the synchronous context manager protocol (`__enter__` / `__exit__`).
3. WHEN a resource is acquired through the Resource_Manager, THE Resource_Manager SHALL track the resource for deterministic cleanup.
4. WHEN the owning workflow completes (successfully, with failure, or via cancellation), THE Resource_Manager SHALL clean up all acquired resources in reverse acquisition order.
5. IF a resource cleanup raises an exception, THEN THE Resource_Manager SHALL log the cleanup failure and continue cleaning up remaining resources.
6. THE Resource_Manager SHALL use Python's `AsyncExitStack` as the underlying implementation for managing resource lifecycles.
7. THE Resource_Manager SHALL be owned by the WorkflowContext so that each workflow has an isolated set of managed resources.
8. THE Resource_Manager SHALL provide typed resource acquisition methods so that callers receive correctly-typed resource references.
9. WHEN a workflow is cancelled, THE Resource_Manager SHALL clean up resources within the cancellation timeout period.

### Requirement 7: Execution Policies

**User Story:** As a platform developer, I want immutable execution policy objects controlling concurrency, retries, and timeouts, so that operational behavior is configurable per workflow without modifying workflow logic.

#### Acceptance Criteria

1. THE Execution_Policy SHALL be a frozen dataclass with fields: max_concurrency, retry_count, retry_backoff_seconds, timeout_seconds, and fail_fast.
2. THE Execution_Policy SHALL provide default values for all fields (max_concurrency=4, retry_count=0, retry_backoff_seconds=1.0, timeout_seconds=300, fail_fast=True).
3. THE Execution_Policy SHALL be immutable after construction so that policy values cannot change during workflow execution.
4. THE Configuration_Subsystem SHALL load default Execution_Policy values from the `[execution]` configuration section.
5. WHEN a workflow is created, THE WorkflowFactory SHALL accept an optional Execution_Policy override, falling back to the configured default policy.
6. THE Execution_Policy SHALL be injectable through the Container as a service so that workflow implementations can access policy values.
7. WHEN retry_count is greater than zero, THE Workflow_Engine SHALL retry failed workflow steps up to retry_count times with exponential backoff starting at retry_backoff_seconds.
8. WHEN timeout_seconds elapses during workflow execution, THE Workflow_Engine SHALL trigger the CancellationToken for that workflow.
9. WHEN fail_fast is true and a workflow step fails, THE Workflow_Engine SHALL cancel remaining steps immediately rather than continuing execution.

### Requirement 8: Contract Interfaces

**User Story:** As a platform developer, I want all kernel components defined as abstract interfaces in `platform/contracts/`, so that implementations are decoupled, replaceable, and testable through dependency injection.

#### Acceptance Criteria

1. THE Platform SHALL define abstract base classes (using `abc.ABC` and `abc.abstractmethod`) in `src/debcraft/platform/contracts/` for: Container, Event_Bus, Workflow_Engine, Configuration_Subsystem, Logger, Resource_Manager, and WorkflowFactory.
2. THE Platform SHALL place all concrete implementations in `src/debcraft/platform/kernel/`.
3. THE Container SHALL resolve services by their abstract interface types, not by concrete implementation types.
4. THE Platform SHALL ensure that `platform/contracts/` imports no modules from `platform/kernel/`, `infrastructure/`, or `plugins/`.
5. WHEN a new kernel component is added, THE Platform SHALL require both an abstract interface in `contracts/` and an implementation in `kernel/`.

### Requirement 9: Cross-Platform and Quality Compliance

**User Story:** As a platform developer, I want all kernel code to pass static analysis, type checking, and cross-platform tests, so that the platform is reliable across environments and maintains consistent code quality.

#### Acceptance Criteria

1. WHEN `uv run ruff check src/debcraft/platform/` is executed, THE Platform SHALL report zero linting violations.
2. WHEN `uv run basedpyright src/debcraft/platform/` is executed, THE Platform SHALL report zero type errors.
3. THE Platform SHALL use `pathlib.Path` for all file system operations within kernel components.
4. THE Platform SHALL not require root or administrator privileges for any kernel operation.
5. THE Platform SHALL use `asyncio` for all I/O-bound operations in kernel components.
6. THE Platform SHALL annotate all public functions and methods with complete type annotations following Google Python Style Guide conventions.
7. THE Platform SHALL provide unit tests marked with `@pytest.mark.unit` for all kernel components covering registration, resolution, dispatch, lifecycle, loading, formatting, acquisition, and policy enforcement.
8. WHEN `uv run pytest -m unit` is executed, THE Platform SHALL pass all kernel component unit tests.

### Requirement 10: Integration and Composition

**User Story:** As a platform developer, I want the kernel components to compose correctly at application startup, so that the DI container wires together the event bus, workflow engine, configuration, logging, and resource management into a functioning runtime.

#### Acceptance Criteria

1. THE Platform SHALL provide a kernel bootstrap function that registers all kernel service implementations in the Container.
2. WHEN the bootstrap function executes, THE Container SHALL have registrations for Event_Bus, Workflow_Engine, Configuration_Subsystem, Logger, Resource_Manager, and WorkflowFactory.
3. THE Workflow_Engine SHALL receive the Event_Bus, Logger, and Configuration_Subsystem through constructor injection during bootstrap.
4. THE WorkflowContext SHALL provide access to the Resource_Manager, CancellationToken, Logger, and Event_Bus for use within workflow implementations.
5. WHEN a workflow is executed end-to-end (created, started, completed), THE Workflow_Engine SHALL publish lifecycle events through the Event_Bus, log state transitions through the Logger, and clean up resources through the Resource_Manager.
6. THE Platform SHALL ensure no component uses module-level mutable global state — all state is owned by service instances managed through the Container.
