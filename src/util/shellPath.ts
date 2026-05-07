const MAGI_BIN = "/home/ocuser/.magi/bin";

export function withMagiBinPath(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const currentPath = env.PATH ?? "";
  if (currentPath.split(":").includes(MAGI_BIN)) {
    return env;
  }

  return {
    ...env,
    PATH: currentPath ? `${MAGI_BIN}:${currentPath}` : MAGI_BIN,
  };
}
