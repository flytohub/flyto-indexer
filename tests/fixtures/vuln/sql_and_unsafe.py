# Covers: SQL_PYTHON_FSTRING / FORMAT / CONCAT + UNSAFE_EVAL / EXEC /
# PICKLE / YAML_LOAD / SUBPROCESS_SHELL + LOG_SENSITIVE
import pickle
import subprocess
import yaml
import logging

logger = logging.getLogger(__name__)


def sql_fstring(user_id, cur):
    cur.execute(f"SELECT * FROM users WHERE id = {user_id}")  # SQL_PYTHON_FSTRING


def sql_format(user_id, cur):
    cur.execute("SELECT * FROM users WHERE id = %s" % (user_id,))  # SQL_PYTHON_FORMAT


def sql_concat(request, cur):
    cur.execute("SELECT * FROM t WHERE name = '" + request.args.get("n") + "'")  # SQL_PYTHON_CONCAT


def rce_eval(code):
    return eval(code)  # UNSAFE_EVAL


def rce_exec(code):
    exec(code)  # UNSAFE_EXEC


def bad_pickle(blob):
    return pickle.loads(blob)  # UNSAFE_PICKLE


def bad_yaml(text):
    return yaml.load(text)  # UNSAFE_YAML_LOAD


def shell_injection(user_input):
    subprocess.run("ls " + user_input, shell=True)  # UNSAFE_SUBPROCESS_SHELL


def leak_secret(password, api_key):
    logger.info(f"creds: password={password} key={api_key}")  # LOG_SENSITIVE
