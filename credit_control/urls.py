from django.urls import path

from . import views
from . import workspace_views
from . import source_views

app_name = "credit_control"

urlpatterns = [
    path("", workspace_views.dashboard, name="dashboard"),
    path("dashboard/", workspace_views.dashboard, name="collections_dashboard"),
    path("debtors/", workspace_views.debtors_list, name="debtors_list"),
    path("debtors/<int:customer_id>/", workspace_views.debtor_detail, name="debtor_detail"),
    path("debtors/<int:customer_id>/follow-up/", views.add_debtor_followup, name="add_debtor_followup"),
    path("debtors/<int:customer_id>/whatsapp/", views.send_debtor_whatsapp, name="send_debtor_whatsapp"),
    path("debtors/<int:customer_id>/statement/", views.statement_customer, name="statement_customer"),
    path("creditors/", workspace_views.creditors_list, name="creditors_list"),
    path("creditors/<int:supplier_id>/", workspace_views.creditor_detail, name="creditor_detail"),
    path("creditors/<int:supplier_id>/follow-up/", views.add_creditor_followup, name="add_creditor_followup"),
    path("followups/", views.followups, name="followups"),
    path("promises/", views.promises, name="promises"),
    path("promises/new/", views.promise_create, name="promise_create"),
    path("promises/<int:pk>/", source_views.promise_detail, name="promise_detail"),
    path("promises/<int:pk>/assign/", source_views.promise_assign, name="promise_assign"),
    path("disputes/", views.disputes, name="disputes"),
    path("disputes/new/", views.dispute_create, name="dispute_create"),
    path("tasks/", views.tasks, name="tasks"),
    path("tasks/new/", views.task_create, name="task_create"),
    path("tasks/<int:pk>/", source_views.task_detail, name="task_detail"),
    path("tasks/<int:pk>/<slug:action>/", source_views.task_action, name="task_action"),
    path("aging/", views.aging_reports, name="aging_reports"),
    path("notifications/", views.notifications, name="notifications"),
    path("reports/<str:report_name>/csv/", views.report_export_csv, name="report_export_csv"),
    path("reports/<str:report_name>/pdf/", views.report_export_pdf, name="report_export_pdf"),
]
