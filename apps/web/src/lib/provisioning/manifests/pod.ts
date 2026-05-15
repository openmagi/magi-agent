import type { PodSpec, ContainerSpec } from "../k8s-client";

export interface PodManifestInput {
  namespace: string;
  botId: string;
  modelSelection: string;
  gatewayImage: string;
  nodeHostImage: string;
  routerImage?: string;
}

export interface PodManifest {
  apiVersion: string;
  kind: string;
  metadata: {
    name: string;
    namespace: string;
    labels: Record<string, string>;
  };
  spec: PodSpec;
}

export function buildPodManifest(input: PodManifestInput): PodManifest {
  const { namespace, botId, modelSelection, gatewayImage, nodeHostImage, routerImage } = input;

  const containers: ContainerSpec[] = [
    // Gateway container
    {
      name: "gateway",
      image: gatewayImage,
      ports: [{ containerPort: 3000, name: "gateway" }],
      resources: {
        requests: { cpu: "100m", memory: "256Mi" },
        limits: { cpu: "500m", memory: "512Mi" },
      },
      volumeMounts: [
        { name: "workspace", mountPath: "/home/ocuser/.openclaw-bot" },
        { name: "config", mountPath: "/home/ocuser/.openclaw-bot/openclaw.json", subPath: "openclaw.json" },
        { name: "secrets", mountPath: "/home/ocuser/.openclaw-bot/secrets", },
      ],
    },
    // Node-host container
    {
      name: "node-host",
      image: nodeHostImage,
      ports: [{ containerPort: 3100, name: "node-host" }],
      resources: {
        requests: { cpu: "100m", memory: "128Mi" },
        limits: { cpu: "500m", memory: "512Mi" },
      },
      volumeMounts: [
        { name: "workspace", mountPath: "/home/ocuser" },
      ],
    },
  ];

  // Optional router sidecar for smart routing
  if (modelSelection === "smart_routing" && routerImage) {
    containers.push({
      name: "iblai-router",
      image: routerImage,
      ports: [{ containerPort: 8402, name: "router" }],
      resources: {
        requests: { cpu: "50m", memory: "64Mi" },
        limits: { cpu: "200m", memory: "256Mi" },
      },
    });
  }

  return {
    apiVersion: "v1",
    kind: "Pod",
    metadata: {
      name: `bot-${botId}`,
      namespace,
      labels: {
        app: "clawy-bot",
        "bot-id": botId,
        component: "gateway",
      },
    },
    spec: {
      containers,
      volumes: [
        {
          name: "workspace",
          persistentVolumeClaim: { claimName: `workspace-${botId}` },
        },
        {
          name: "config",
          configMap: { name: `bot-config-${botId}` },
        },
        {
          name: "secrets",
          secret: { secretName: `bot-secrets-${botId}` },
        },
      ],
      restartPolicy: "Always",
    },
  };
}
