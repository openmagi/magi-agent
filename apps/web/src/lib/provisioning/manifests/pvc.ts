export interface PVCManifest {
  apiVersion: string;
  kind: string;
  metadata: {
    name: string;
    namespace: string;
    labels: Record<string, string>;
  };
  spec: {
    accessModes: string[];
    resources: {
      requests: {
        storage: string;
      };
    };
  };
}

export function buildPVCManifest(namespace: string, botId: string, sizeMb: number): PVCManifest {
  return {
    apiVersion: "v1",
    kind: "PersistentVolumeClaim",
    metadata: {
      name: `workspace-${botId}`,
      namespace,
      labels: {
        app: "clawy-bot",
        "bot-id": botId,
      },
    },
    spec: {
      accessModes: ["ReadWriteOnce"],
      resources: {
        requests: {
          storage: `${sizeMb}Mi`,
        },
      },
    },
  };
}
