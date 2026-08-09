from django.db import connection
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def health(request):
    """Liveness + DB connectivity check, used by the frontend to prove the stack is up."""
    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False
    return Response({"status": "ok", "db": "ok" if db_ok else "unreachable"})
