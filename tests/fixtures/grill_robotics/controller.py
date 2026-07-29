"""Deterministic robot capabilities used by the Grill integration fixture."""


class CapabilityRegistry:
    """Expose bounded capabilities to the workflow planner."""

    def __init__(self):
        self.capabilities = {
            "follow_line": {"max_speed_mps": 0.4},
            "stop": {"latency_ms": 50},
            "wait_until_clear": {"timeout_seconds": 30},
        }

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


def compile_route(colours: list[str]) -> list[dict]:
    """Compile route colours into deterministic workflow steps."""
    return [
        {"capability": "follow_line", "colour": colour, "stop_on_obstacle": True}
        for colour in colours
    ]
