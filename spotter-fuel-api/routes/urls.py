"""Routes app URL configuration."""

from django.urls import path
from .views import RoutePlanView, dashboard_view

app_name = "routes"

urlpatterns = [
    path("", dashboard_view, name="dashboard"),
    path("api/route/", RoutePlanView.as_view(), name="plan_route"),
]
