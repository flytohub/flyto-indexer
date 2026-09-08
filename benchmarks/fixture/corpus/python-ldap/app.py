import ldap


def find_user(request, conn):
    uid = request.args.get("uid")
    return conn.search_s("dc=example", ldap.SCOPE_SUBTREE, "(uid=" + uid + ")")
