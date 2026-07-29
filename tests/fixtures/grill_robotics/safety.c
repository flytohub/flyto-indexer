#include <stdbool.h>
#include <stdint.h>

typedef struct {
    bool motor_enabled;
    uint32_t stopped_at_ms;
} safety_state_t;

/**
 * Hard real-time boundary. Higher-level planners can request a stop but cannot
 * bypass this deterministic controller.
 */
void emergency_stop(safety_state_t *state, uint32_t now_ms) {
    if (state == 0) {
        return;
    }
    state->motor_enabled = false;
    state->stopped_at_ms = now_ms;
}

bool motion_is_safe(const safety_state_t *state) {
    return state != 0 && state->motor_enabled;
}
