import { Building2, DoorOpen, Layers, LayoutPanelLeft, Rows3, type LucideIcon } from "lucide-react";

import { isNodeType, type NodeType } from "@/lib/projects/types";

const ICONS: Record<NodeType, LucideIcon> = {
  stage: Layers,
  building: Building2,
  section: LayoutPanelLeft,
  floor: Rows3,
  room: DoorOpen,
};

export function NodeIcon({ type, className }: { type: string; className?: string }) {
  const Icon = isNodeType(type) ? ICONS[type] : Layers;
  return <Icon className={className ?? "size-4"} aria-hidden />;
}
