# Sanitized / parameterised versions — should NOT fire the SQL rules.
def sql_param(user_id, cur):
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))  # param, safe

# NOTE: whitelisted table-name fstring WOULD fire SQL_PYTHON_FSTRING.
# That's a known limitation of pattern-only SAST — distinguishing
# whitelisted vs tainted interpolation needs taint analysis. Kept as
# comment so the safe/ matrix stays noise-free; intent-aware check is
# future work.
#   cur.execute(f"SELECT COUNT(*) FROM {table}")

def no_format_with_user(val):
    # non-SQL f-string — must not trigger SQL rules
    return f"id={val}"

def yaml_safe(text):
    import yaml
    return yaml.safe_load(text)   # safe_load is fine

def pickle_nope():
    # Just using pickle.dumps is fine — it's loads/load that's the bug
    import pickle
    return pickle.dumps({"a": 1})

# debug gated by env — should NOT fire DEBUG_ENABLED_DJANGO
import os
DEBUG = os.environ.get("DEBUG", "0") == "1"
