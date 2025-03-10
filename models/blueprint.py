from flask import Blueprint

blueprint = Blueprint(
    "prism-ctf-models",
    __name__,
    template_folder="../templates",
    static_folder="../assets",
)
