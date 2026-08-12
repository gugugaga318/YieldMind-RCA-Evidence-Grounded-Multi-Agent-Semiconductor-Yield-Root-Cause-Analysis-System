import { AlertTriangle, CheckCircle2, CircleDashed, LoaderCircle } from "lucide-react";

import type { TaskStatus } from "../types";

interface StatusBadgeProps {
  status: TaskStatus;
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const Icon =
    status === "completed"
      ? CheckCircle2
      : status === "failed" || status === "cancelled"
        ? AlertTriangle
        : status === "running" || status === "cancel_requested"
          ? LoaderCircle
          : CircleDashed;

  return (
    <span className={`status-badge status-${status}`}>
      <Icon size={14} aria-hidden="true" />
      {status}
    </span>
  );
}
