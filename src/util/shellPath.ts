const OPENCLAW_BIN = "/home/ocuser/.openclaw/bin";

export function withOpenclawBinPath(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const currentPath = env.PATH ?? "";
  if (currentPath.split(":").includes(OPENCLAW_BIN)) {
    return env;
  }

  return {
    ...env,
    PATH: currentPath ? `${OPENCLAW_BIN}:${currentPath}` : OPENCLAW_BIN,
  };
}
