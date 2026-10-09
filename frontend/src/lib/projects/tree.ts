import type { TreeNode } from "./types";

export type TreeIndex = {
  byId: Map<string, TreeNode>;
  children: Map<string | null, TreeNode[]>;
};

export type TreeRow = {
  node: TreeNode;
  depth: number;
  hasChildren: boolean;
  expanded: boolean;
};

export function buildIndex(nodes: TreeNode[]): TreeIndex {
  const byId = new Map<string, TreeNode>();
  const children = new Map<string | null, TreeNode[]>();
  for (const node of nodes) {
    byId.set(node.id, node);
    const siblings = children.get(node.parent_id);
    if (siblings) siblings.push(node);
    else children.set(node.parent_id, [node]);
  }
  for (const siblings of children.values()) {
    siblings.sort(
      (a, b) =>
        a.sort_order - b.sort_order ||
        a.name.localeCompare(b.name, "ru", { numeric: true }) ||
        a.id.localeCompare(b.id),
    );
  }
  return { byId, children };
}

export const MAX_SEARCH_MATCHES = 300;

export type SearchResult = {
  matches: Set<string>;
  shown: Set<string>;
  truncated: boolean;
};

// Совпавшие узлы и все их предки: дерево при поиске показывает путь к каждому результату.
export function search(index: TreeIndex, query: string): SearchResult {
  const needle = query.trim().toLowerCase();
  const matches = new Set<string>();
  const shown = new Set<string>();
  let truncated = false;
  if (needle === "") return { matches, shown, truncated };
  for (const node of index.byId.values()) {
    const haystack = `${node.name} ${node.code ?? ""}`.toLowerCase();
    if (!haystack.includes(needle)) continue;
    if (matches.size >= MAX_SEARCH_MATCHES) {
      truncated = true;
      break;
    }
    matches.add(node.id);
    for (const id of node.path) shown.add(id);
  }
  return { matches, shown, truncated };
}

// Плоский список видимых строк: корни, затем раскрытые ветки. shown (поиск) сужает показ и
// раскрывает все ветки, которые ведут к результатам.
export function visibleRows(
  index: TreeIndex,
  expanded: ReadonlySet<string>,
  shown: ReadonlySet<string> | null,
): TreeRow[] {
  const rows: TreeRow[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const node of index.children.get(parent) ?? []) {
      if (shown && !shown.has(node.id)) continue;
      const kids = (index.children.get(node.id) ?? []).filter((kid) => !shown || shown.has(kid.id));
      const open = shown ? kids.length > 0 : expanded.has(node.id);
      rows.push({ node, depth, hasChildren: kids.length > 0, expanded: open });
      if (open) walk(node.id, depth + 1);
    }
  };
  walk(null, 0);
  return rows;
}

export function ancestors(index: TreeIndex, node: TreeNode): TreeNode[] {
  return node.path
    .slice(0, -1)
    .map((id) => index.byId.get(id))
    .filter((item): item is TreeNode => item !== undefined);
}

export function pathLabel(index: TreeIndex, node: TreeNode): string {
  return node.path.map((id) => index.byId.get(id)?.name ?? "…").join(" › ");
}

export function siblingsOf(index: TreeIndex, node: TreeNode): TreeNode[] {
  return index.children.get(node.parent_id) ?? [];
}
