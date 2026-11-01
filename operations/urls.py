from django.urls import path
from . import views

app_name='operations'
urlpatterns=[
    path('',views.home,name='home'),
    path('new/',views.configure,name='configure'),
    path('<int:pk>/',views.detail,name='detail'),
    path('<int:pk>/responsibility/',views.configure,name='configure_existing'),
]
