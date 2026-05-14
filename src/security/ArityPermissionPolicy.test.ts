import { describe, it, expect } from "vitest";
import {
  evaluateArityPermission,
  DEFAULT_ARITY_RULES,
  type ArityPermissionRule,
} from "./ArityPermissionPolicy.js";

describe("ArityPermissionPolicy.evaluateArityPermission", () => {
  describe("with default rules", () => {
    it("allows simple echo command", () => {
      const r = evaluateArityPermission("echo hello", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("allow");
    });

    it("allows npm run dev", () => {
      const r = evaluateArityPermission("npm run dev", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("allow");
    });

    it("asks for git push", () => {
      const r = evaluateArityPermission("git push origin main", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("ask");
      expect(r.matchedRule?.pattern).toBe("git push *");
    });

    it("asks for git reset", () => {
      const r = evaluateArityPermission("git reset --hard HEAD~1", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("ask");
      expect(r.matchedRule?.pattern).toBe("git reset *");
    });

    it("denies git checkout --", () => {
      const r = evaluateArityPermission("git checkout -- file.txt", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("deny");
      expect(r.matchedRule?.pattern).toBe("git checkout -- *");
    });

    it("denies git clean", () => {
      const r = evaluateArityPermission("git clean -fd", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("deny");
      expect(r.matchedRule?.pattern).toBe("git clean *");
    });

    it("asks for rm", () => {
      const r = evaluateArityPermission("rm -rf node_modules", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("ask");
      expect(r.matchedRule?.pattern).toBe("rm *");
    });

    it("denies sudo", () => {
      const r = evaluateArityPermission("sudo apt install vim", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("deny");
      expect(r.matchedRule?.pattern).toBe("sudo *");
    });

    it("asks for curl", () => {
      const r = evaluateArityPermission("curl https://example.com", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("ask");
      expect(r.matchedRule?.pattern).toBe("curl *");
    });

    it("asks for kubectl", () => {
      const r = evaluateArityPermission("kubectl apply -f deploy.yaml", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("ask");
      expect(r.matchedRule?.pattern).toBe("kubectl *");
    });

    it("asks for docker", () => {
      const r = evaluateArityPermission("docker run alpine", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("ask");
      expect(r.matchedRule?.pattern).toBe("docker *");
    });
  });

  describe("multi-segment commands", () => {
    it("takes most restrictive action across pipe segments", () => {
      const r = evaluateArityPermission("echo hello | sudo tee /etc/hosts", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("deny");
    });

    it("takes most restrictive across && segments", () => {
      const r = evaluateArityPermission("npm run build && git push origin main", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("ask");
    });

    it("allows when all segments are safe", () => {
      const r = evaluateArityPermission("echo a && echo b", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("allow");
    });
  });

  describe("empty / edge cases", () => {
    it("allows empty command", () => {
      const r = evaluateArityPermission("", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("allow");
      expect(r.semanticPrefix).toBe("");
    });

    it("allows whitespace-only command", () => {
      const r = evaluateArityPermission("   ", DEFAULT_ARITY_RULES);
      expect(r.action).toBe("allow");
    });
  });

  describe("custom rules", () => {
    it("last matching rule wins", () => {
      const rules: ArityPermissionRule[] = [
        { pattern: "docker *", action: "deny" },
        { pattern: "docker compose *", action: "allow" },
      ];
      const r = evaluateArityPermission("docker compose up -d", rules);
      expect(r.action).toBe("allow");
    });

    it("respects custom allow override", () => {
      const rules: ArityPermissionRule[] = [
        { pattern: "kubectl *", action: "deny" },
        { pattern: "kubectl get *", action: "allow" },
        { pattern: "* *", action: "allow" },
      ];
      const r = evaluateArityPermission("kubectl get pods", rules);
      expect(r.action).toBe("allow");
    });

    it("empty rules = allow everything", () => {
      const r = evaluateArityPermission("sudo rm -rf /", []);
      expect(r.action).toBe("allow");
    });
  });

  describe("semanticPrefix in result", () => {
    it("includes the semantic pattern", () => {
      const r = evaluateArityPermission("git push origin main", DEFAULT_ARITY_RULES);
      expect(r.semanticPrefix).toBe("git push *");
    });

    it("includes multiple patterns for multi-segment", () => {
      const r = evaluateArityPermission("npm test && git push", DEFAULT_ARITY_RULES);
      expect(r.semanticPrefix).toContain("npm test *");
      expect(r.semanticPrefix).toContain("git push *");
    });
  });
});
