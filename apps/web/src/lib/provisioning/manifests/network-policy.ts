export interface NetworkPolicyManifest {
  apiVersion: string;
  kind: string;
  metadata: {
    name: string;
    namespace: string;
  };
  spec: {
    podSelector: Record<string, unknown>;
    policyTypes: string[];
    ingress?: Record<string, unknown>[];
    egress?: Record<string, unknown>[];
  };
}

export function buildNetworkPolicy(namespace: string): NetworkPolicyManifest {
  return {
    apiVersion: "networking.k8s.io/v1",
    kind: "NetworkPolicy",
    metadata: {
      name: "bot-network-policy",
      namespace,
    },
    spec: {
      podSelector: {},
      policyTypes: ["Ingress", "Egress"],
      ingress: [
        // Allow ingress from chat-proxy in clawy-system namespace (for mobile app)
        {
          ports: [{ protocol: "TCP", port: 8080 }],
          from: [
            {
              namespaceSelector: {
                matchLabels: { "kubernetes.io/metadata.name": "clawy-system" },
              },
              podSelector: {
                matchLabels: { app: "chat-proxy" },
              },
            },
          ],
        },
      ],
      egress: [
        // Allow DNS resolution (required for all outbound)
        {
          ports: [
            { protocol: "UDP", port: 53 },
            { protocol: "TCP", port: 53 },
          ],
        },
        // Allow egress to external web services (HTTP/HTTPS + common alt ports)
        {
          ports: [
            { protocol: "TCP", port: 80 },
            { protocol: "TCP", port: 443 },
            { protocol: "TCP", port: 8080 },
            { protocol: "TCP", port: 8090 },
          ],
          to: [
            {
              ipBlock: { cidr: "0.0.0.0/0" },
            },
          ],
        },
        // Allow inter-pod communication within namespace
        {
          to: [
            {
              podSelector: {},
            },
          ],
        },
        // Allow egress to api-proxy in clawy-system namespace
        {
          ports: [{ protocol: "TCP", port: 3001 }],
          to: [
            {
              namespaceSelector: {
                matchLabels: { "kubernetes.io/metadata.name": "clawy-system" },
              },
              podSelector: {
                matchLabels: { app: "api-proxy" },
              },
            },
          ],
        },
        // Allow egress to chat-proxy in clawy-system namespace (for email API)
        {
          ports: [{ protocol: "TCP", port: 3002 }],
          to: [
            {
              namespaceSelector: {
                matchLabels: { "kubernetes.io/metadata.name": "clawy-system" },
              },
              podSelector: {
                matchLabels: { app: "chat-proxy" },
              },
            },
          ],
        },
        // Allow egress to document-worker in clawy-system namespace (PDF/DOCX/XLSX conversion)
        {
          ports: [{ protocol: "TCP", port: 3009 }],
          to: [
            {
              namespaceSelector: {
                matchLabels: { "kubernetes.io/metadata.name": "clawy-system" },
              },
              podSelector: {
                matchLabels: { app: "document-worker" },
              },
            },
          ],
        },
      ],
    },
  };
}
