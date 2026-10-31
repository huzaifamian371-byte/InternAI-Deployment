from flask import Blueprint

roadmap_bp = Blueprint("roadmap", __name__, url_prefix="/roadmap")

from . import routes  # noqa: E402, F401
