"""Compatibility adapter; all next-work guidance uses the Stage 7 operator."""


def suggest_next_work(*, user, exclude_task=None, limit=3):
    if not user or not user.is_authenticated or not user.is_active or not user.has_perm("tasker.change_task") or not user.has_perm("tasker.view_task"):
        return []
    from tasker.services.operator import build_operator
    result = build_operator(user=user, exclude_task=exclude_task, persist=False)
    candidates = ([result["now"]] if result["now"] else []) + result["next"]
    return [item["task"] for item in candidates][:max(1, min(int(limit), 3))]
