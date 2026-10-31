from flask import Blueprint

analyzer_bp = Blueprint("analyzer", __name__, url_prefix="/analyzer")

from . import routes  # noqa: E402, F401
