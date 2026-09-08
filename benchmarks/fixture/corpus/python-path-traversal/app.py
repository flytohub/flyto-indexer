def read_report(request):
    name = request.args.get("name")
    return open(name).read()
