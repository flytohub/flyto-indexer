from db_utils import run_query


def handle_request(request):
    user_id = request.args.get("id")
    query = f"SELECT * FROM users WHERE id = {user_id}"
    run_query(query)
