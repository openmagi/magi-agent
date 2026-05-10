export interface StartStop {
  start(): Promise<void>;
  stop(): Promise<void>;
}

export interface BootstrapCoreAgentOptions {
  agent: StartStop;
  http: StartStop;
}

export async function bootstrapCoreAgent({
  agent,
  http,
}: BootstrapCoreAgentOptions): Promise<void> {
  await http.start();
  try {
    await agent.start();
  } catch (err) {
    await http.stop().catch(() => undefined);
    throw err;
  }
}
