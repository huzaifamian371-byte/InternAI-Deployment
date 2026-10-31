from flask import Blueprint

risk_bp = Blueprint("risk", __name__, url_prefix="/risk")

from . import routes  # noqa: E402, F401
