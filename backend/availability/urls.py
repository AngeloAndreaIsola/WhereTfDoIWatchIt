"""Availability URL routes."""
from django.urls import path

from . import candidate_admin_views

urlpatterns = [
    path(
        "admin/provider-content/candidates/",
        candidate_admin_views.provider_content_candidates_list,
        name="admin-provider-content-candidates-list",
    ),
    path(
        "admin/provider-content/<int:pk>/candidates/",
        candidate_admin_views.provider_content_candidates_detail,
        name="admin-provider-content-candidates-detail",
    ),
    path(
        "admin/provider-content/<int:pk>/candidates/<int:tmdb_id>/accept/",
        candidate_admin_views.candidate_accept,
        name="admin-provider-content-candidate-accept",
    ),
    path(
        "admin/provider-content/<int:pk>/candidates/<int:tmdb_id>/reject/",
        candidate_admin_views.candidate_reject,
        name="admin-provider-content-candidate-reject",
    ),
]
