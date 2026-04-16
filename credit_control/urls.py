from django.urls import path

from . import views

app_name = "credit_control"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/", views.dashboard, name="collections_dashboard"),
    path("debtors/", views.debtors_list, name="debtors_list"),
    path("debtors/<int:customer_id>/", views.debtor_detail, name="debtor_detail"),
    path("debtors/<int:customer_id>/follow-up/", views.add_debtor_followup, name="add_debtor_followup"),
    path("debtors/<int:customer_id>/whatsapp/", views.send_debtor_whatsapp, name="send_debtor_whatsapp"),
    path("debtors/<int:customer_id>/statement/", views.statement_customer, name="statement_customer"),
    path("creditors/", views.creditors_list, name="creditors_list"),
    path("creditors/<int:supplier_id>/", views.creditor_detail, name="creditor_detail"),
    path("creditors/<int:supplier_id>/follow-up/", views.add_creditor_followup, name="add_creditor_followup"),
    path("followups/", views.followups, name="followups"),
    path("promises/", views.promises, name="promises"),
    path("promises/new/", views.promise_create, name="promise_create"),
    path("disputes/", views.disputes, name="disputes"),
    path("disputes/new/", views.dispute_create, name="dispute_create"),
    path("tasks/", views.tasks, name="tasks"),
    path("tasks/new/", views.task_create, name="task_create"),
    path("aging/", views.aging_reports, name="aging_reports"),
    path("notifications/", views.notifications, name="notifications"),
    path("reports/<str:report_name>/csv/", views.report_export_csv, name="report_export_csv"),
    path("reports/<str:report_name>/pdf/", views.report_export_pdf, name="report_export_pdf"),
]
