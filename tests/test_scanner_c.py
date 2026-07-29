"""Tests for the dependency-free C/C++ scanner."""

from pathlib import Path

from src.scanner.c import CScanner


C_SOURCE = """
#include <stdbool.h>
#include "robot/safety.h"

typedef struct {
    bool motor_enabled;
    unsigned int stopped_at_ms;
} safety_state_t;

void emergency_stop(safety_state_t *state, unsigned int now_ms) {
    if (state == 0) {
        return;
    }
    log_stop(now_ms);
    state->motor_enabled = false;
}

bool motion_is_safe(const safety_state_t *state) {
    return state != 0 && state->motor_enabled;
}
"""


def test_supported_c_and_cpp_extensions():
    scanner = CScanner("robotics")
    assert {".c", ".h", ".cpp", ".hpp"}.issubset(scanner.supported_extensions)


def test_extracts_functions_struct_fields_includes_and_calls():
    scanner = CScanner("robotics")
    symbols, dependencies = scanner.scan_file(Path("src/safety.c"), C_SOURCE)

    by_name = {symbol.name: symbol for symbol in symbols}
    assert {"safety_state_t", "emergency_stop", "motion_is_safe"} <= set(by_name)
    assert by_name["emergency_stop"].language == "c"
    assert by_name["emergency_stop"].returns == "void"
    assert by_name["emergency_stop"].params == [
        "safety_state_t *state",
        "unsigned int now_ms",
    ]
    assert by_name["safety_state_t"].metadata["fields"] == [
        {"name": "motor_enabled", "type": "bool"},
        {"name": "stopped_at_ms", "type": "unsigned int"},
    ]

    imports = [edge for edge in dependencies if edge.dep_type.value == "imports"]
    calls = [edge for edge in dependencies if edge.dep_type.value == "calls"]
    assert {edge.target_id for edge in imports} == {"stdbool.h", "robot/safety.h"}
    assert any(
        edge.source_id.endswith(":function:emergency_stop")
        and edge.target_id == "log_stop"
        for edge in calls
    )


def test_ignores_prototypes_control_flow_comments_and_strings():
    source = """
void prototype_only(int value);
// void fake_comment(void) { fake_call(); }
const char *text = "void fake_string(void) { nope(); }";

static inline int real_function(int value) {
    while (value > 0) {
        value--;
    }
    return value;
}
"""
    scanner = CScanner("robotics")
    symbols, dependencies = scanner.scan_file(Path("include/safety.h"), source)

    assert [symbol.name for symbol in symbols] == ["real_function"]
    assert all(edge.target_id not in {"while", "fake_call", "nope"} for edge in dependencies)


def test_extracts_cpp_file_with_cpp_language_tag():
    scanner = CScanner("robotics")
    symbols, _ = scanner.scan_file(
        Path("src/adapter.cpp"),
        "int execute_capability(int command) { return command; }\n",
    )

    assert symbols[0].name == "execute_capability"
    assert symbols[0].language == "cpp"
