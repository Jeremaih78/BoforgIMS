from __future__ import annotations

from .services.workflows import auto_create_collection_tasks, push_alerts, refresh_credit_control_snapshots


def run_daily_credit_control_jobs():
    snapshots = refresh_credit_control_snapshots()
    created_tasks = auto_create_collection_tasks()
    push_alerts()
    return {
        "snapshots": snapshots,
        "created_tasks": len(created_tasks),
    }
