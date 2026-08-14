# Implementation Plan: M1 Platform Kernel

## Overview

Build the domain-agnostic runtime kernel for DebCraft providing seven core components: dependency injection container, event bus, workflow engine, configuration service, structured logging, resource manager, and execution policies. Components are built bottom-up — foundational pieces with no internal dependencies first, then components that build on them, then the bootstrap function that wires everything together, and finally comprehensive tests.

## Tasks

- [x] 1. Foundation: Dependencies, error hierarchy, and execution policies
  - [x] 1.1 Add hypothesis dev dependency and create platform package structure
    - Add `"hypothesis>=6.100"` to `[dependency-groups] dev` in `pyproject.toml`
    - Create directory structure: `src/debcraft/platform/__init__.py`, `src/debcraft/platform/contracts/__init__.py`, `src/debcraft/platform/kernel/__init__.py`
    - Ensure `platform/contracts/__init__.py` re-exports all ABCs (initially empty, populated as contracts are added)
    - Ensure `platform/kernel/__init__.py` re-exports `bootstrap`
    - _Requirements: 8.1, 8.2_

  - [x] 1.2 Implement error hierarchy in `src/debcraft/platform/kernel/errors.py`
    - Create `PlatformError(Exception)` base class
    - Create `ContainerError(PlatformError)`, `ServiceNotFoundError(ContainerError)`, `CircularDependencyError(ContainerError)`
    - Create `ConfigurationError(PlatformError)`, `ConfigurationSyntaxError(ConfigurationError)`, `ConfigurationValidationError(ConfigurationError)`
    - Create `WorkflowError(PlatformError)`, `WorkflowTimeoutError(WorkflowError)`
    - Create `ResourceCleanupError(PlatformError)`
    - All errors should have descriptive `__init__` methods with relevant context parameters
    - _Requirements: 1.5, 1.10, 4.6, 4.10_

  - [x] 1.3 Implement execution policies contract and kernel implementation
    - Create `src/debcraft/platform/contracts/policies.py` with `ExecutionPolicy` frozen dataclass
    - Fields: `max_concurrency=4`, `retry_count=0`, `retry_backoff_seconds=1.0`, `timeout_seconds=300.0`, `fail_fast=True`
    - Create `src/debcraft/platform/kernel/policies.py` re-exporting the dataclass (contract and implementation are the same for value objects)
    - _Requirements: 7.1, 7.2, 7.3_

- [x] 2. Dependency injection container
  - [x] 2.1 Implement container contract in `src/debcraft/platform/contracts/container.py`
    - Define `Scope` ABC with `resolve()` and `close()` abstract methods
    - Define `Container` ABC with `register_singleton()`, `register_transient()`, `register_scoped()`, `register_instance()`, `resolve()`, `create_scope()` abstract methods
    - Use `TypeVar("T")` for type-safe generic resolution
    - _Requirements: 1.1, 1.2, 1.3, 1.6, 1.7, 8.1_

  - [x] 2.2 Implement `KernelContainer` and `KernelScope` in `src/debcraft/platform/kernel/container.py`
    - Create `Lifetime` enum (SINGLETON, TRANSIENT, SCOPED) and `ServiceRegistration` dataclass
    - Implement `KernelContainer` with registration dict mapping `type → (implementation_type, lifetime)`
    - Implement resolution via `__init__.__annotations__` introspection for constructor injection
    - Implement `_resolution_stack: set[type]` for circular dependency detection raising `CircularDependencyError`
    - Cache singleton instances in `dict[type, Any]`
    - Raise `ServiceNotFoundError` for unregistered types
    - Implement `KernelScope` inheriting parent container singletons with its own scoped instance cache
    - Implement `Scope.close()` disposing all scoped instances
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10_

  - [x]* 2.3 Write property tests for container (Properties 1-6)
    - **Property 1: Singleton resolution identity** — resolving N times returns same object
    - **Property 2: Transient resolution distinctness** — resolving N times returns N distinct objects
    - **Property 3: Scoped resolution isolation** — same scope returns same instance, different scope returns different instance
    - **Property 4: Constructor injection resolution** — resolved instance has correct dependency types
    - **Property 5: Circular dependency detection** — cycles raise `CircularDependencyError`
    - **Property 6: Scope disposal completeness** — closing scope disposes all scoped instances
    - File: `tests/unit/platform/kernel/test_container_properties.py`
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.8, 1.9**

  - [x]* 2.4 Write unit tests for container
    - Test singleton registration and resolution
    - Test transient creates new instances each time
    - Test scoped isolation between scopes
    - Test `register_instance()` for pre-built instances
    - Test `ServiceNotFoundError` for unregistered types
    - Test `CircularDependencyError` with descriptive chain message
    - Test constructor injection with multiple dependencies
    - Test type-safe resolution returns correctly-typed references
    - File: `tests/unit/platform/kernel/test_container.py`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10_

- [x] 3. Event bus
  - [x] 3.1 Implement event bus contract in `src/debcraft/platform/contracts/events.py`
    - Define `DomainEvent` frozen dataclass with `event_type: str`, `timestamp: datetime`, `correlation_id: UUID`
    - Define `EventHandler` type alias for sync and async callables
    - Define `EventBus` ABC with `subscribe()`, `unsubscribe()`, `publish()` abstract methods
    - _Requirements: 2.1, 2.9, 8.1_

  - [x] 3.2 Implement `KernelEventBus` in `src/debcraft/platform/kernel/events.py`
    - Implement `_handlers: dict[type, list[EventHandler]]` maintaining insertion order
    - Implement `publish()` iterating handlers in order, using `inspect.iscoroutinefunction()` for async detection
    - Catch handler exceptions, log with ERROR level, continue dispatch to remaining handlers
    - Implement `unsubscribe()` removing specific handler from the list
    - Publishing to event type with zero handlers is a no-op
    - Define workflow event dataclasses: `WorkflowStartedEvent`, `WorkflowCompletedEvent`, `WorkflowFailedEvent`, `WorkflowCancelledEvent`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10_

  - [x]* 3.3 Write property tests for event bus (Properties 7-11)
    - **Property 7: Event dispatch completeness and ordering** — all N handlers invoked in registration order
    - **Property 8: Handler isolation on failure** — K failing handlers don't prevent remaining (N-K) from running
    - **Property 9: Correlation ID propagation** — all handlers receive event with same correlation_id
    - **Property 10: Unsubscribe removes handler from dispatch** — unsubscribed handler not invoked on subsequent publish
    - **Property 11: Frozen dataclass immutability** — assigning to DomainEvent attribute raises FrozenInstanceError
    - File: `tests/unit/platform/kernel/test_events_properties.py`
    - **Validates: Requirements 2.2, 2.3, 2.5, 2.6, 2.7, 2.8, 2.9**

  - [x]* 3.4 Write unit tests for event bus
    - Test subscribe and dispatch single handler
    - Test sync and async handler support
    - Test handler registration ordering
    - Test handler exception isolation with logging
    - Test unsubscribe removes handler
    - Test publish to zero handlers is no-op
    - Test DomainEvent immutability
    - File: `tests/unit/platform/kernel/test_events.py`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10_

- [x] 4. Configuration and logging (parallel-safe)
  - [x] 4.1 Implement configuration contract in `src/debcraft/platform/contracts/configuration.py`
    - Define `ConfigurationService` ABC with `get_section()`, `register_section()`, `reload()` abstract methods
    - Use `TypeVar("T")` for typed section retrieval
    - _Requirements: 4.1, 8.1_

  - [x] 4.2 Implement `KernelConfigurationService` in `src/debcraft/platform/kernel/configuration.py`
    - Implement load order: built-in defaults → `~/.config/debcraft/config.toml` → `.debcraft.toml` → env vars → CLI args
    - Use `tomllib.loads()` for TOML parsing
    - Implement environment variable mapping: `DEBCRAFT_SECTION__KEY` → `{"section": {"key": value}}`
    - Implement deep-merge of dicts at each layer (later overrides earlier)
    - Produce frozen dataclass instances for each registered section type
    - Validation in `__post_init__` of section dataclasses
    - Raise `ConfigurationSyntaxError` on TOML parse errors, `ConfigurationValidationError` on invalid values
    - Define `LoggingConfig`, `ExecutionConfig`, `PlatformConfig` frozen dataclasses
    - Support plugin section registration
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10_

  - [x]* 4.3 Write property tests for configuration (Properties 15-17)
    - **Property 15: Configuration precedence** — highest precedence layer value wins
    - **Property 16: Environment variable mapping** — `DEBCRAFT_SECTION__KEY` maps correctly
    - **Property 17: Configuration validation rejects invalid values** — invalid field raises `ConfigurationError`
    - File: `tests/unit/platform/kernel/test_configuration_properties.py`
    - **Validates: Requirements 4.2, 4.3, 4.4, 4.6, 4.10**

  - [x] 4.4 Implement logging contract in `src/debcraft/platform/contracts/logging.py`
    - Define `Logger` ABC with `debug()`, `info()`, `warning()`, `error()`, `with_correlation_id()` abstract methods
    - Define `LoggerFactory` ABC with `get_logger()` abstract method
    - _Requirements: 5.1, 5.2, 8.1_

  - [x] 4.5 Implement `KernelLoggerFactory` and formatters in `src/debcraft/platform/kernel/logging.py`
    - Wrap Python's `logging` module — each `Logger` maps to a `logging.Logger` with component name
    - Implement `HumanFormatter`: `TIMESTAMP LEVEL COMPONENT MESSAGE [correlation_id=...]`
    - Implement `JsonFormatter`: JSON object with `timestamp`, `level`, `component`, `message`, `correlation_id`, `extra` fields
    - Use `contextvars.ContextVar[UUID | None]` for correlation ID propagation
    - Implement `with_correlation_id()` returning a child logger bound to that ID via a logging Filter
    - Rich console handler integration to avoid display corruption when live displays are active
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9_

  - [x]* 4.6 Write property tests for logging (Properties 18-19)
    - **Property 18: Human log format structure** — output contains timestamp, level, component, message in order; includes correlation_id when active
    - **Property 19: JSON log format structure** — output is valid JSON with required keys
    - File: `tests/unit/platform/kernel/test_logging_properties.py`
    - **Validates: Requirements 5.1, 5.3, 5.4, 5.5**

  - [x]* 4.7 Write unit tests for configuration and logging
    - Test TOML loading with valid and invalid files
    - Test precedence ordering (env vars override file values)
    - Test environment variable mapping with double underscore separator
    - Test frozen dataclass section production
    - Test `ConfigurationSyntaxError` on malformed TOML
    - Test `ConfigurationValidationError` on invalid values
    - Test human format output structure
    - Test JSON format output is valid JSON with required keys
    - Test correlation ID inclusion when active
    - Test component name attachment
    - File: `tests/unit/platform/kernel/test_configuration.py`, `tests/unit/platform/kernel/test_logging.py`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.10, 5.1, 5.3, 5.4, 5.5_

- [x] 5. Resource manager
  - [x] 5.1 Implement resource manager contract in `src/debcraft/platform/contracts/resources.py`
    - Define `ResourceManager` ABC with `acquire_async()`, `acquire_sync()`, `cleanup()` abstract methods
    - Use `TypeVar("T")` for typed resource acquisition
    - _Requirements: 6.1, 6.2, 6.8, 8.1_

  - [x] 5.2 Implement `KernelResourceManager` in `src/debcraft/platform/kernel/resources.py`
    - Wrap `contextlib.AsyncExitStack`
    - `acquire_async()` calls `stack.enter_async_context(resource)`
    - `acquire_sync()` calls `stack.enter_context(resource)`
    - `cleanup()` calls `await stack.aclose()` unwinding in reverse order
    - Catch individual cleanup failures, log ERROR, continue cleaning remaining
    - Each instance owned by a single `WorkflowContext`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9_

  - [x]* 5.3 Write property tests for resource manager (Properties 20-21)
    - **Property 20: Resource cleanup reverse ordering** — N resources cleaned up in LIFO order
    - **Property 21: Resource cleanup isolation on failure** — K failing cleanups don't prevent remaining (N-K) from being called
    - File: `tests/unit/platform/kernel/test_resources_properties.py`
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5**

  - [x]* 5.4 Write unit tests for resource manager
    - Test async resource acquisition and cleanup
    - Test sync resource acquisition and cleanup
    - Test reverse-order cleanup
    - Test cleanup failure isolation (one failing doesn't stop others)
    - Test empty cleanup (no resources acquired)
    - File: `tests/unit/platform/kernel/test_resources.py`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [x] 6. Workflow engine
  - [x] 6.1 Implement workflow contract in `src/debcraft/platform/contracts/workflow.py`
    - Define `WorkflowState` enum (CREATED, RUNNING, COMPLETED, FAILED, CANCELLED)
    - Define `WorkflowSummary` frozen dataclass
    - Define `CancellationToken` class with `is_cancelled` property and `cancel()` method
    - Define `ProgressReporter` ABC with `report()` method
    - Define `WorkflowContext` class with scope, cancellation_token, progress, resources, logger, event_bus
    - Define `Workflow` ABC with `name` property and `execute()` coroutine
    - Define `WorkflowEngine` ABC with `run()`, `cancel()`, `shutdown()` methods
    - Define `WorkflowFactory` ABC with `create()` method
    - _Requirements: 3.1, 3.5, 3.7, 3.8, 3.9, 8.1_

  - [x] 6.2 Implement `KernelWorkflowEngine` and `KernelWorkflowFactory` in `src/debcraft/platform/kernel/workflow.py`
    - `KernelWorkflowEngine` holds references to EventBus, LoggerFactory, and Container
    - On `run()`: transition Created→Running, execute workflow, transition to terminal state
    - Publish `WorkflowStartedEvent`, `WorkflowCompletedEvent`, `WorkflowFailedEvent`, or `WorkflowCancelledEvent` on state transitions
    - Implement timeout enforcement via `asyncio.wait_for()` with `timeout_seconds` from ExecutionPolicy
    - Implement retry logic with exponential backoff (B, 2B, 4B, ...) up to `retry_count` attempts
    - Implement fail-fast: cancel remaining steps on first failure when `fail_fast=True`
    - Implement SIGINT handling via `asyncio.get_event_loop().add_signal_handler()` triggering cancellation for all tracked workflows
    - Generate `WorkflowSummary` on terminal state with workflow_name, start_time, end_time, final_state, error_details
    - Implement `KernelWorkflowFactory` creating workflow instances with injected WorkflowContext
    - Implement a default `ProgressReporter` (e.g., logging-based)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 7.5, 7.7, 7.8, 7.9_

  - [x]* 6.3 Write property tests for workflow engine (Properties 12-14, 22-24)
    - **Property 12: Workflow summary generation** — terminal state produces valid WorkflowSummary
    - **Property 13: Workflow lifecycle event publishing** — run publishes WorkflowStartedEvent + exactly one terminal event
    - **Property 14: CancellationToken monotonic transition** — once cancelled, stays cancelled
    - **Property 22: Retry with exponential backoff** — failing step retried N times with correct delays
    - **Property 23: Timeout triggers cancellation** — exceeding T seconds triggers CancellationToken
    - **Property 24: Fail-fast cancels remaining steps** — step K failure prevents steps K+1..N execution
    - File: `tests/unit/platform/kernel/test_workflow_properties.py`
    - **Validates: Requirements 3.5, 3.8, 3.11, 7.7, 7.8, 7.9**

  - [x]* 6.4 Write unit tests for workflow engine
    - Test workflow lifecycle transitions (Created → Running → Completed)
    - Test failed workflow transitions and error recording
    - Test cancellation token cooperative cancellation
    - Test progress reporter receives percentage and message
    - Test WorkflowSummary generation on completion, failure, and cancellation
    - Test WorkflowFactory creates workflows with injected dependencies
    - Test timeout enforcement triggers cancellation
    - Test retry with exponential backoff
    - Test fail-fast cancels remaining steps
    - File: `tests/unit/platform/kernel/test_workflow.py`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.11, 7.7, 7.8, 7.9_

- [x] 7. Bootstrap and integration
  - [x] 7.1 Implement bootstrap function in `src/debcraft/platform/kernel/bootstrap.py`
    - Import all contracts and kernel implementations
    - Create `KernelContainer` instance
    - Register `ConfigurationService → KernelConfigurationService` as singleton
    - Register `EventBus → KernelEventBus` as singleton
    - Register `LoggerFactory → KernelLoggerFactory` as singleton
    - Register `WorkflowEngine → KernelWorkflowEngine` as singleton
    - Register `WorkflowFactory → KernelWorkflowFactory` as singleton
    - Register `ResourceManager → KernelResourceManager` as scoped
    - Return the configured `Container`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x] 7.2 Update `platform/contracts/__init__.py` to re-export all ABCs and `platform/kernel/__init__.py` to re-export `bootstrap`
    - `contracts/__init__.py` should export: `Container`, `Scope`, `EventBus`, `DomainEvent`, `WorkflowEngine`, `Workflow`, `WorkflowFactory`, `WorkflowContext`, `WorkflowState`, `WorkflowSummary`, `CancellationToken`, `ProgressReporter`, `ConfigurationService`, `LoggerFactory`, `Logger`, `ResourceManager`, `ExecutionPolicy`
    - `kernel/__init__.py` should export: `bootstrap`
    - _Requirements: 8.1, 8.4_

  - [x]* 7.3 Write property test for bootstrap (Property 25)
    - **Property 25: Bootstrap completeness** — resolving each kernel contract interface from bootstrapped container succeeds
    - File: `tests/unit/platform/kernel/test_bootstrap.py`
    - **Validates: Requirements 10.1, 10.2**

  - [x]* 7.4 Write unit tests for bootstrap and integration
    - Test bootstrap returns a Container with all expected registrations
    - Test resolving each service interface succeeds
    - Test WorkflowEngine receives EventBus, LoggerFactory via constructor injection
    - Test end-to-end: create workflow, run through engine, verify events published and resources cleaned
    - File: `tests/unit/platform/kernel/test_bootstrap.py`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

- [x] 8. Architecture tests and static analysis compliance
  - [x] 8.1 Write architecture tests in `tests/architecture/test_platform_architecture.py`
    - Test `platform/contracts/` imports no modules from `platform/kernel/`, `infrastructure/`, or `plugins/`
    - Test all ABCs in contracts have corresponding implementations in kernel
    - Test no component uses module-level mutable global state
    - Add import-linter contract in `pyproject.toml` if not already covered: `platform.contracts` must not import `platform.kernel`
    - Use `@pytest.mark.architecture` marker
    - _Requirements: 8.3, 8.4, 8.5, 10.6_

  - [x] 8.2 Ensure ruff and basedpyright pass on all platform code
    - Fix any linting or type errors in `src/debcraft/platform/`
    - Ensure all public functions have complete type annotations following Google Python Style Guide
    - Ensure all docstrings are present per ruff rules
    - _Requirements: 9.1, 9.2, 9.6_

- [x] 9. Final checkpoint
  - Ensure all tests pass (`uv run pytest -m unit`, `uv run pytest -m architecture`), ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document (25 properties total)
- Unit tests validate specific examples and edge cases
- The design uses Python throughout — all implementations use Python 3.13+ features
- All components use `asyncio` for I/O-bound operations per requirement 9.5
- `pathlib.Path` is used for all filesystem operations per requirement 9.3

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1", "4.4", "5.1", "6.1"] },
    { "id": 2, "tasks": ["2.2", "3.2", "4.2", "4.5", "5.2"] },
    { "id": 3, "tasks": ["2.3", "2.4", "3.3", "3.4", "4.3", "4.6", "4.7", "5.3", "5.4"] },
    { "id": 4, "tasks": ["6.2"] },
    { "id": 5, "tasks": ["6.3", "6.4"] },
    { "id": 6, "tasks": ["7.1", "7.2"] },
    { "id": 7, "tasks": ["7.3", "7.4", "8.1", "8.2"] }
  ]
}
```
