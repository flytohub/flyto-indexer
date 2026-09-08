def offset(request, line):
    marker = request.args.get("marker")
    return line.find(",") + len(marker)
