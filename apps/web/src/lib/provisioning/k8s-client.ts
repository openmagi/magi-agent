import * as k8s from "@kubernetes/client-node";
import stream from "stream";

/**
 * Required namespace label for bot namespaces.
 *
 * NetworkPolicy selectors on api-proxy, chat-proxy, browser-worker, and
 * x402-gateway match `namespaceSelector.matchLabels.clawy-bot=true` to
 * permit ingress from bot pods. If a bot namespace is missing this label,
 * ingress to platform services will silently fail (ECONNREFUSED), leaving
 * the bot in a broken state that is painful to diagnose.
 *
 * Enforcement lives in two places:
 *   1. createNamespace() post-write readback (defence in depth)
 *   2. scripts/audit-bot-namespace-labels.sh (cluster-wide audit + --fix)
 */
export const BOT_NAMESPACE_LABEL_KEY = "clawy-bot";
export const BOT_NAMESPACE_LABEL_VALUE = "true";

/**
 * Verify a namespace readback contains the required bot label.
 * Returns null on success, or an error message on failure.
 * Exported for testability — pure function, no k8s client dependency.
 */
export function verifyBotNamespaceLabel(
  name: string,
  readBackLabels: Record<string, string> | undefined | null,
): string | null {
  const actual = readBackLabels?.[BOT_NAMESPACE_LABEL_KEY];
  if (actual === BOT_NAMESPACE_LABEL_VALUE) return null;
  return (
    `namespace ${name} created but required label ` +
    `${BOT_NAMESPACE_LABEL_KEY}=${BOT_NAMESPACE_LABEL_VALUE} ` +
    `is missing (got: ${JSON.stringify(readBackLabels ?? {})}). ` +
    `NetworkPolicy ingress to api-proxy will silently fail.`
  );
}

export interface ContainerSpec {
  name: string;
  image: string;
  ports?: { containerPort: number; name?: string }[];
  env?: ({ name: string; value: string } | { name: string; valueFrom: { secretKeyRef: { name: string; key: string } } })[];
  volumeMounts?: { name: string; mountPath: string; subPath?: string; readOnly?: boolean }[];
  resources?: {
    requests?: { cpu?: string; memory?: string };
    limits?: { cpu?: string; memory?: string };
  };
  command?: string[];
  args?: string[];
  lifecycle?: {
    preStop?: {
      exec?: { command: string[] };
    };
  };
  securityContext?: {
    runAsNonRoot?: boolean;
    runAsUser?: number;
    allowPrivilegeEscalation?: boolean;
    readOnlyRootFilesystem?: boolean;
    capabilities?: { drop?: string[] };
    seccompProfile?: { type: string };
  };
}

export interface PodSpec {
  initContainers?: ContainerSpec[];
  containers: ContainerSpec[];
  volumes?: {
    name: string;
    persistentVolumeClaim?: { claimName: string };
    configMap?: { name: string };
    secret?: { secretName: string; optional?: boolean };
    emptyDir?: Record<string, never>;
  }[];
  imagePullSecrets?: { name: string }[];
  restartPolicy?: string;
  terminationGracePeriodSeconds?: number;
  nodeSelector?: Record<string, string>;
  tolerations?: {
    key?: string;
    operator?: string;
    value?: string;
    effect?: string;
  }[];
}

export interface NetworkPolicySpec {
  apiVersion: string;
  kind: string;
  metadata: { name: string; namespace: string };
  spec: Record<string, unknown>;
}

export interface K8sClient {
  createNamespace(name: string, labels?: Record<string, string>): Promise<void>;
  deleteNamespace(name: string): Promise<void>;
  namespaceExists(name: string): Promise<boolean>;
  createPVC(namespace: string, name: string, sizeMb: number): Promise<void>;
  createSecret(
    namespace: string,
    name: string,
    data: Record<string, string>
  ): Promise<void>;
  getSecret(namespace: string, name: string): Promise<Record<string, string> | null>;
  createPod(
    namespace: string,
    name: string,
    spec: PodSpec
  ): Promise<void>;
  deletePod(namespace: string, name: string): Promise<void>;
  getPodStatus(namespace: string, name: string): Promise<string>;
  getPodLogs(
    namespace: string,
    name: string,
    container?: string
  ): Promise<string>;
  areContainersReady(namespace: string, name: string): Promise<boolean>;
  replaceSecret(
    namespace: string,
    name: string,
    data: Record<string, string>
  ): Promise<void>;
  execInPod(namespace: string, podName: string, container: string, command: string[]): Promise<string>;
  getPodVolumeUsageBytes(namespace: string, podName: string, volumeName: string): Promise<number>;
  applyNetworkPolicy(namespace: string, manifest: NetworkPolicySpec): Promise<void>;
  getClusterAllocatableMemoryMi(): Promise<number>;
}

/** Parse K8s memory string (e.g. "8043816Ki", "16Gi", "8388608") to MiB */
function parseK8sMemoryToMi(memStr: string): number {
  const match = memStr.match(/^(\d+)(Ki|Mi|Gi|Ti)?$/);
  if (!match) return 0;
  const value = parseInt(match[1], 10);
  const unit = match[2] || "";
  switch (unit) {
    case "Ki": return Math.floor(value / 1024);
    case "Mi": return value;
    case "Gi": return value * 1024;
    case "Ti": return value * 1024 * 1024;
    default: return Math.floor(value / (1024 * 1024)); // bytes to MiB
  }
}

function getK8sStatusCode(error: unknown): number | null {
  if (typeof error !== "object" || error === null || !("response" in error)) return null;
  const response = (error as { response?: { statusCode?: number; status?: number } }).response;
  return response?.statusCode ?? response?.status ?? null;
}

export function createK8sClient(): K8sClient {
  const kc = new k8s.KubeConfig();
  if (process.env.KUBECONFIG_CONTENT) {
    kc.loadFromString(process.env.KUBECONFIG_CONTENT);
  } else {
    kc.loadFromDefault();
  }

  const coreApi = kc.makeApiClient(k8s.CoreV1Api);
  const networkingApi = kc.makeApiClient(k8s.NetworkingV1Api);

  return {
    async createNamespace(name: string, labels?: Record<string, string>): Promise<void> {
      const ns: k8s.V1Namespace = {
        metadata: { name, labels },
      };
      await coreApi.createNamespace({ body: ns });

      // Preflight: read back the namespace and verify the required bot label
      // is present. NetworkPolicy selectors depend on this label; a silent
      // drop here causes ECONNREFUSED from bot pods to platform services.
      // We fail fast rather than leaving a broken namespace behind.
      const required =
        labels?.[BOT_NAMESPACE_LABEL_KEY] === BOT_NAMESPACE_LABEL_VALUE;
      if (required) {
        const readBack = await coreApi.readNamespace({ name });
        const err = verifyBotNamespaceLabel(
          name,
          readBack?.metadata?.labels,
        );
        if (err) throw new Error(err);
      }
    },

    async deleteNamespace(name: string): Promise<void> {
      await coreApi.deleteNamespace({ name });
      // Wait for namespace to fully terminate (up to 60s)
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
          const ns = await coreApi.readNamespace({ name });
          if (ns?.status?.phase === "Terminating") continue;
        } catch {
          // 404 = namespace gone
          return;
        }
      }
    },

    async namespaceExists(name: string): Promise<boolean> {
      try {
        const ns = await coreApi.readNamespace({ name });
        return ns?.status?.phase === "Active";
      } catch {
        return false;
      }
    },

    async createPVC(
      namespace: string,
      name: string,
      sizeMb: number
    ): Promise<void> {
      const pvc: k8s.V1PersistentVolumeClaim = {
        metadata: { name, namespace },
        spec: {
          accessModes: ["ReadWriteOnce"],
          resources: {
            requests: {
              storage: `${sizeMb}Mi`,
            },
          },
        },
      };
      await coreApi.createNamespacedPersistentVolumeClaim({
        namespace,
        body: pvc,
      });
    },

    async createSecret(
      namespace: string,
      name: string,
      data: Record<string, string>
    ): Promise<void> {
      // Encode all values to base64
      const encodedData: Record<string, string> = {};
      for (const [key, value] of Object.entries(data)) {
        encodedData[key] = Buffer.from(value).toString("base64");
      }

      const secret: k8s.V1Secret = {
        metadata: { name, namespace },
        type: "Opaque",
        data: encodedData,
      };
      await coreApi.createNamespacedSecret({ namespace, body: secret });
    },

    async getSecret(namespace: string, name: string): Promise<Record<string, string> | null> {
      try {
        const secret = await coreApi.readNamespacedSecret({ namespace, name });
        const decoded: Record<string, string> = {};
        for (const [key, value] of Object.entries(secret.data ?? {})) {
          decoded[key] = Buffer.from(value, "base64").toString("utf8");
        }
        return decoded;
      } catch (error) {
        if (getK8sStatusCode(error) === 404) return null;
        throw error;
      }
    },

    async replaceSecret(
      namespace: string,
      name: string,
      data: Record<string, string>
    ): Promise<void> {
      try {
        await coreApi.deleteNamespacedSecret({ namespace, name });
      } catch {
        // 404 = secret didn't exist, safe to ignore
      }

      const encodedData: Record<string, string> = {};
      for (const [key, value] of Object.entries(data)) {
        encodedData[key] = Buffer.from(value).toString("base64");
      }

      const secret: k8s.V1Secret = {
        metadata: { name, namespace },
        type: "Opaque",
        data: encodedData,
      };
      await coreApi.createNamespacedSecret({ namespace, body: secret });
    },

    async createPod(
      namespace: string,
      name: string,
      spec: PodSpec
    ): Promise<void> {
      const mapContainer = (c: ContainerSpec): k8s.V1Container => ({
        name: c.name,
        image: c.image,
        ports: c.ports?.map((p) => ({
          containerPort: p.containerPort,
          name: p.name,
        })),
        env: c.env?.map((e) => {
          if ("valueFrom" in e) {
            return { name: e.name, valueFrom: e.valueFrom };
          }
          return { name: e.name, value: e.value };
        }),
        volumeMounts: c.volumeMounts?.map((vm) => ({
          name: vm.name,
          mountPath: vm.mountPath,
          subPath: vm.subPath,
          readOnly: vm.readOnly,
        })),
        resources: c.resources
          ? {
              requests: c.resources.requests,
              limits: c.resources.limits,
            }
          : undefined,
        command: c.command,
        args: c.args,
        lifecycle: c.lifecycle,
        imagePullPolicy: "IfNotPresent",
        securityContext: c.securityContext,
      });

      const containers = spec.containers.map(mapContainer);
      const initContainers = spec.initContainers?.map(mapContainer);

      const volumes: k8s.V1Volume[] | undefined = spec.volumes?.map((v) => ({
        name: v.name,
        persistentVolumeClaim: v.persistentVolumeClaim
          ? { claimName: v.persistentVolumeClaim.claimName }
          : undefined,
        configMap: v.configMap
          ? { name: v.configMap.name }
          : undefined,
        secret: v.secret
          ? { secretName: v.secret.secretName, optional: v.secret.optional }
          : undefined,
        emptyDir: v.emptyDir ? {} : undefined,
      }));

      const pod: k8s.V1Pod = {
        metadata: { name, namespace },
        spec: {
          initContainers,
          containers,
          volumes,
          imagePullSecrets: spec.imagePullSecrets?.map((s) => ({ name: s.name })),
          restartPolicy: spec.restartPolicy ?? "Always",
          terminationGracePeriodSeconds: spec.terminationGracePeriodSeconds,
          nodeSelector: spec.nodeSelector,
          tolerations: spec.tolerations,
        },
      };
      await coreApi.createNamespacedPod({ namespace, body: pod });
    },

    async deletePod(namespace: string, name: string): Promise<void> {
      await coreApi.deleteNamespacedPod({ namespace, name });
    },

    async getPodStatus(namespace: string, name: string): Promise<string> {
      const response = await coreApi.readNamespacedPod({ namespace, name });
      return response?.status?.phase ?? "Unknown";
    },

    async getPodLogs(
      namespace: string,
      name: string,
      container?: string
    ): Promise<string> {
      const response = await coreApi.readNamespacedPodLog({
        namespace,
        name,
        container,
        tailLines: 100,
      });
      return typeof response === "string" ? response : "";
    },

    async areContainersReady(namespace: string, name: string): Promise<boolean> {
      const pod = await coreApi.readNamespacedPod({ namespace, name });
      const statuses = pod?.status?.containerStatuses ?? [];
      return statuses.length > 0 && statuses.every((cs) => cs.ready);
    },

    async applyNetworkPolicy(namespace: string, manifest: NetworkPolicySpec): Promise<void> {
      const body: k8s.V1NetworkPolicy = {
        metadata: { name: manifest.metadata.name, namespace },
        spec: manifest.spec as k8s.V1NetworkPolicySpec,
      };
      await networkingApi.createNamespacedNetworkPolicy({ namespace, body });
    },

    async getClusterAllocatableMemoryMi(): Promise<number> {
      const nodeList = await coreApi.listNode();
      let totalMi = 0;
      for (const node of nodeList.items ?? []) {
        const memStr = node.status?.allocatable?.["memory"];
        if (memStr) {
          totalMi += parseK8sMemoryToMi(memStr);
        }
      }
      return totalMi;
    },

    async getPodVolumeUsageBytes(
      namespace: string,
      podName: string,
      volumeName: string,
    ): Promise<number> {
      // Use kubelet stats API (REST) instead of exec (WebSocket/SPDY)
      const pod = await coreApi.readNamespacedPod({ namespace, name: podName });
      const nodeName = pod?.spec?.nodeName;
      if (!nodeName) return 0;

      const statsRaw = await coreApi.connectGetNodeProxyWithPath({
        name: nodeName,
        path: "stats/summary",
      });
      const stats: { pods?: { podRef?: { namespace?: string; name?: string }; volume?: { name?: string; usedBytes?: number }[] }[] } =
        typeof statsRaw === "string" ? JSON.parse(statsRaw) : statsRaw;

      for (const p of stats.pods ?? []) {
        if (p.podRef?.namespace === namespace && p.podRef?.name === podName) {
          for (const vol of p.volume ?? []) {
            if (vol.name === volumeName) {
              return vol.usedBytes ?? 0;
            }
          }
        }
      }
      return 0;
    },

    async execInPod(
      namespace: string,
      podName: string,
      container: string,
      command: string[],
    ): Promise<string> {
      const exec = new k8s.Exec(kc);
      const stdout = new stream.PassThrough();
      const stderr = new stream.PassThrough();

      let stdoutData = "";
      let stderrData = "";
      stdout.on("data", (chunk: Buffer) => { stdoutData += chunk.toString(); });
      stderr.on("data", (chunk: Buffer) => { stderrData += chunk.toString(); });

      return new Promise<string>((resolve, reject) => {
        const timeout = setTimeout(() => {
          stdout.destroy();
          stderr.destroy();
          reject(new Error("exec timeout"));
        }, 15000);

        exec
          .exec(namespace, podName, container, command, stdout, stderr, null, false, (status) => {
            clearTimeout(timeout);
            if (status.status === "Success") {
              resolve(stdoutData);
            } else {
              reject(new Error(stderrData || status.message || "exec failed"));
            }
          })
          .catch((err) => {
            clearTimeout(timeout);
            reject(err);
          });
      });
    },
  };
}
