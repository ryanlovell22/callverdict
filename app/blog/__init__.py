from flask import Blueprint

bp = Blueprint("blog", __name__)

from . import routes  # noqa: E402, F401
