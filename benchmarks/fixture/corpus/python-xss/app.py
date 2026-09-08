from markupsafe import Markup


def show(request):
    fragment = request.args.get("q")
    return Markup(fragment)
