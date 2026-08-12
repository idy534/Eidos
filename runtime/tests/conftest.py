from __future__ import annotations

import pytest

# Test layers are assigned at collection time so product tests stay focused on
# behavior instead of repeating infrastructure markers in every file. Keep this
# list conservative: Fast is the default; suites that cross durable/process/Git
# boundaries are explicitly promoted to Integration, Platform, and/or Slow.
INTEGRATION_FILES = frozenset(
    {
        "test_application_boundary.py",
        "test_application_protocol_routing.py",
        "test_approval_application.py",
        "test_checkpoint_lineage.py",
        "test_checkpoint_managed_worktree.py",
        "test_corrective_integration.py",
        "test_direct_workspace.py",
        "test_event_delivery_recovery.py",
        "test_events_operations.py",
        "test_execution_attempt_persistence.py",
        "test_extension_storage.py",
        "test_git_backend.py",
        "test_git_observation_integrity.py",
        "test_git_process_hardening.py",
        "test_git_typed_backend_closure.py",
        "test_git_worktree_kernel.py",
        "test_managed_run_admission.py",
        "test_mcp.py",
        "test_mcp_sandbox.py",
        "test_model_persistence.py",
        "test_phase2_runtime.py",
        "test_phase3_runtime.py",
        "test_phase3a_thread_execution.py",
        "test_worktree_retention_restore.py",
        "test_phase5a_runtime_lifecycle.py",
        "test_phase5b0_runtime_hardening.py",
        "test_phase5b_fault_injection.py",
        "test_phase5b_protocol_finalization.py",
        "test_phase5b_tool_execution.py",
        "test_phase5b_workspace_manifest.py",
        "test_phase5c_async_operations.py",
        "test_phase5c_event_outbox.py",
        "test_phase5c_fault_wiring.py",
        "test_phase5c_parallel_convergence.py",
        "test_phase5c_process_recovery.py",
        "test_phase5c_resource_registry.py",
        "test_phase5c_tool_state_machine.py",
        "test_phase5c_workspace_index.py",
        "test_plugins.py",
        "test_repository_application_persistence.py",
        "test_repository_persistence.py",
        "test_repository_watcher.py",
        "test_response_actions.py",
        "test_runtime_distribution.py",
        "test_runtime_loop.py",
        "test_runtime_reliability_regressions.py",
        "test_sandbox_permissions.py",
        "test_seatbelt.py",
        "test_seatbelt_file_commit_cache.py",
        "test_server.py",
        "test_session_managed_worktree.py",
        "test_session_run_application.py",
        "test_shell.py",
        "test_storage_compatibility.py",
        "test_storage_schema.py",
        "test_tool_execution.py",
        "test_tool_orchestrator.py",
        "test_worktree_lifecycle.py",
    }
)

PLATFORM_FILES = frozenset(
    {
        "test_checkpoint_managed_worktree.py",
        "test_git_backend.py",
        "test_git_observation_integrity.py",
        "test_git_process_hardening.py",
        "test_git_typed_backend_closure.py",
        "test_git_worktree_kernel.py",
        "test_mcp.py",
        "test_mcp_sandbox.py",
        "test_phase3a_thread_execution.py",
        "test_worktree_retention_restore.py",
        "test_phase5c_process_recovery.py",
        "test_runtime_distribution.py",
        "test_sandbox_permissions.py",
        "test_seatbelt.py",
        "test_seatbelt_file_commit_cache.py",
        "test_server.py",
        "test_session_managed_worktree.py",
        "test_shell.py",
        "test_worktree_lifecycle.py",
    }
)

SLOW_FILES = frozenset(
    {
        "test_async_kernel.py",
        "test_checkpoint_managed_worktree.py",
        "test_event_delivery_recovery.py",
        "test_git_backend.py",
        "test_git_typed_backend_closure.py",
        "test_git_worktree_kernel.py",
        "test_managed_run_admission.py",
        "test_mcp.py",
        "test_phase3a_thread_execution.py",
        "test_worktree_retention_restore.py",
        "test_phase5c_process_recovery.py",
        "test_server.py",
        "test_session_managed_worktree.py",
        "test_shell.py",
        "test_worktree_lifecycle.py",
    }
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        filename = item.path.name
        if filename in INTEGRATION_FILES:
            item.add_marker(pytest.mark.integration)
        if filename in PLATFORM_FILES:
            item.add_marker(pytest.mark.platform)
        if filename in SLOW_FILES:
            item.add_marker(pytest.mark.slow)
