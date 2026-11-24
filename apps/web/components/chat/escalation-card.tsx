import { Headset } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { EscalationEvent } from "@/lib/generated/agent-events";

export function EscalationCard({ escalation }: { escalation: EscalationEvent }) {
  const urgent = escalation.priority === "high" || escalation.priority === "urgent";
  return (
    <div className="mt-3 rounded-xl border border-amber-500/30 bg-amber-500/5 p-3" role="status">
      <div className="flex items-center gap-2">
        <Headset className="size-4 text-amber-600" aria-hidden />
        <span className="text-xs font-medium">Handed to a support specialist</span>
        <Badge variant={urgent ? "destructive" : "outline"} className="ml-auto capitalize">
          {escalation.priority} priority
        </Badge>
      </div>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Ticket</dt>
        <dd className="font-mono">{escalation.ticket_id}</dd>
        <dt className="text-muted-foreground">First reply</dt>
        <dd>within {escalation.first_response_hours} hours</dd>
        <dt className="text-muted-foreground">Summary</dt>
        <dd className="text-muted-foreground">{escalation.reason}</dd>
      </dl>
    </div>
  );
}
