"""Canonical action links and compact presentation for the single operator."""
from django.urls import reverse


def present_operator(result):
    decisions = []
    global_codes = {"HOURS_UNKNOWN", "OUTSIDE_HOURS", "OVERCAPACITY", "NO_CAPACITY", "CAPABILITY"}
    grouped = {}
    routes = {
        "HOURS_UNKNOWN": ("work_policy", "Configure work hours"),
        "OUTSIDE_HOURS": ("availability_home", "Review unavailable time"),
        "OVERCAPACITY": ("planning_home", "Review the overloaded plan"),
        "NO_CAPACITY": ("planning_home", "Review time blocks"),
        "EFFORT_UNKNOWN": ("task_effort", "Estimate remaining work"),
        "OUTCOME_REVIEW": ("task_effort", "Review remaining work"),
        "FOCUS_EFFORT_REVIEW": ("task_effort", "Update remaining effort"),
        "DEFINE_DONE": ("task_edit", "Clarify the outcome"),
        "CAPABILITY": ("task_list", "Review Work"),
        "CANDIDATE_LIMIT": ("task_list", "Review all Work"),
        "FOCUS_RESTRICTED": ("task_list", "Stop your own timer from Work"),
        "SOURCES_CHANGED": ("operator_home", "Refresh guidance"),
        "ACCESS_REVIEW": ("task_list", "Review assignment access"),
    }
    for original in result["decisions"]:
        decision = dict(original)
        code, task = decision.get("code"), decision.get("task")
        if code in global_codes and code in grouped:
            grouped[code]["affected_count"] += 1
            continue
        route = routes.get(code)
        if route:
            name, decision["action_label"] = route
            args = (task.slug,) if name in {"task_effort", "task_edit"} and task else ()
            decision["url"] = reverse("ims:tasker:" + name, args=args)
        elif task:
            decision["url"], decision["action_label"] = task.get_absolute_url(), "Review task"
        elif code == "FOCUS_REVIEW" and result.get("running_entry"):
            decision["url"] = reverse("ims:tasker:focus_mode", args=(result["running_entry"].task.slug,))
            decision["action_label"] = "Review current Focus"
        else:
            decision["url"], decision["action_label"] = reverse("ims:tasker:task_list"), "Review Work"
        if code in global_codes:
            decision.update(task=None, task_id=None, affected_count=1,
                title={"HOURS_UNKNOWN": "Set your available hours", "OUTSIDE_HOURS": "Protect your unavailable time",
                       "OVERCAPACITY": "Make the plan feasible", "NO_CAPACITY": "Make room for focused work",
                       "CAPABILITY": "Review your access"}[code])
            grouped[code] = decision
        decisions.append(decision)
    return {**result, "decisions": decisions}
