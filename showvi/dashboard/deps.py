"""Common FastAPI dependencies for dashboard requests — single-user mode."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from dashboard.auth import get_current_user, get_or_create_preference
from dashboard.state import MonitorSelection, get_monitor_state
from dashboard.workspace import WorkspaceContext, get_workspace_for_user
from dashboard.user_stub import User, UserPreference


@dataclass
class DashboardContext:
    user: User
    workspace: WorkspaceContext
    preference: UserPreference
    monitor: MonitorSelection


async def get_dashboard_context(request: Request, user: User = Depends(get_current_user)) -> DashboardContext:
    workspace = get_workspace_for_user(user)
    preference = get_or_create_preference(user)
    monitor = get_monitor_state(user.id)
    request.state.dashboard_user = user
    request.state.dashboard_workspace = workspace
    return DashboardContext(user=user, workspace=workspace, preference=preference, monitor=monitor)
