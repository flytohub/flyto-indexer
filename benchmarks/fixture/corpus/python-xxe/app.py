from lxml import etree


def parse(request):
    document = request.data
    return etree.fromstring(document)
