from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("role/", views.set_role, name="role"),
    path("new/<slug:key>/", views.new_request, name="new"),
    path("r/<str:ref>/", views.detail, name="detail"),
    path("r/<str:ref>/pdf/", views.pdf, name="pdf"),
    path("r/<str:ref>/<str:name>/", views.action, name="action"),
]