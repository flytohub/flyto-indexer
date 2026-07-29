/** Provider-neutral adapter contract used by the Grill integration fixture. */
export interface RobotAdapter {
  readonly capabilities: ReadonlySet<string>;
  executeCapability(name: string, input: unknown): Promise<unknown>;
  stop(reason: string): Promise<void>;
}

export class SimulatedRobotAdapter implements RobotAdapter {
  readonly capabilities = new Set(["follow_line", "stop", "wait_until_clear"]);

  async executeCapability(name: string, input: unknown): Promise<unknown> {
    if (!this.capabilities.has(name)) {
      throw new Error(`Unsupported capability: ${name}`);
    }
    return { name, input, simulated: true };
  }

  async stop(reason: string): Promise<void> {
    void reason;
  }
}
