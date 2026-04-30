const CLAWY_BIN = "/home/ocuser/.clawy/bin";

export function withClawyBinPath(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const currentPath = env.PATH ?? "";
  if (currentPath.split(":").includes(CLAWY_BIN)) {
    return env;
  }

  return {
    ...env,
    PATH: currentPath ? `${CLAWY_BIN}:${currentPath}` : CLAWY_BIN,
  };
}
