import flask


def render(request):
    template = request.args.get("t")
    return flask.render_template_string(template)
