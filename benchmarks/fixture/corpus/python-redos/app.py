import re


def match(request):
    pattern = request.args.get("p")
    return re.search(pattern, "aaaaaaaaaaaaaaaa")
