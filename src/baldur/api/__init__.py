"""
API layer for the baldur system.

This module contains REST API implementations for various frameworks.

Available APIs:
- django: Django REST Framework views, serializers, and URLs

Usage:
    # In your Django project's urls.py:
    from baldur.api.django import urls as baldur_urls

    urlpatterns = [
        path('api/baldur/', include(baldur_urls)),
    ]

Status: Internal
"""

# API implementations are optional

__all__ = []

# Try to import Django API. Any failure (missing DRF, misconfigured Django
# settings, etc.) means the Django API is unavailable in this process —
# framework-free callers such as baldur.api.admin must still import cleanly.
try:
    from baldur.api import django

    __all__.append("django")
except Exception:
    pass
