export const API = "/api/v1";

export const PROJECTS_KEY = ["projects"] as const;
export const projectKey = (id: string) => ["projects", id] as const;
export const treeKey = (id: string) => ["projects", id, "tree"] as const;
export const nodeKey = (projectId: string, nodeId: string) =>
  ["projects", projectId, "node", nodeId] as const;
export const LEGAL_ENTITIES_REF_KEY = ["refs", "legal-entities"] as const;
export const CUSTOMERS_REF_KEY = ["refs", "customers"] as const;
export const USERS_REF_KEY = ["refs", "users"] as const;
export const WORKS_KEY = ["works"] as const;
export const WORK_CATEGORIES_KEY = ["works", "categories"] as const;
