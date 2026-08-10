from django.urls import path

from . import views

app_name = "tasker"

urlpatterns = [
    path("", views.index, name="index"),
    path("ai/usage/", views.ai_usage_dashboard, name="ai_usage_dashboard"),
    path("ai/assistant/", views.assistant_home, name="assistant_home"),
    path("ai/assistant/<slug:action>/", views.assistant_day_action, name="assistant_day_action"),
    path("planner/", views.daily_plan, name="daily_plan"),
    path("planner/review/", views.daily_review, name="daily_review"),
    path("tasks/", views.task_list, name="task_list"),
    path("tasks/new/", views.task_create, name="task_create"),
    path("tasks/<slug:slug>/", views.task_detail, name="task_detail"),
    path("tasks/<slug:slug>/ai/<slug:action>/", views.assistant_task_action, name="assistant_task_action"),
    path("tasks/<slug:slug>/ai/steps/<int:message_pk>/apply/", views.assistant_apply_steps, name="assistant_apply_steps"),
    path("tasks/<slug:slug>/edit/", views.task_edit, name="task_edit"),
    path("tasks/<slug:slug>/delete/", views.task_delete, name="task_delete"),
    path("tasks/<slug:slug>/comments/add/", views.comment_add, name="comment_add"),
    path("tasks/<slug:slug>/checklist/add/", views.checklist_add, name="checklist_add"),
    path(
        "tasks/<slug:slug>/checklist/<int:item_pk>/toggle/",
        views.checklist_toggle,
        name="checklist_toggle",
    ),
    path("tasks/<slug:slug>/time/add/", views.time_add, name="time_add"),
    path("tasks/<slug:slug>/progress/", views.progress_update, name="progress_update"),
    path("tasks/<slug:slug>/focus/", views.focus_mode, name="focus_mode"),
    path("tasks/<slug:slug>/focus/start/", views.focus_start, name="focus_start"),
    path("tasks/<slug:slug>/focus/pause/", views.focus_pause, name="focus_pause"),
    path("tasks/<slug:slug>/focus/resume/", views.focus_resume, name="focus_resume"),
    path("tasks/<slug:slug>/focus/stop/", views.focus_stop, name="focus_stop"),
    path("tasks/<slug:slug>/focus/block/", views.focus_block, name="focus_block"),
    path("tasks/<slug:slug>/focus/complete/", views.focus_complete, name="focus_complete"),
    path("tasks/<slug:slug>/focus/notes/", views.focus_notes, name="focus_notes"),
    path(
        "tasks/<slug:slug>/focus/checklist/<int:item_pk>/toggle/",
        views.focus_checklist_toggle,
        name="focus_checklist_toggle",
    ),
]
