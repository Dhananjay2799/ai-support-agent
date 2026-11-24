import { ChatPanel } from "@/components/chat/chat-panel";
import { Badge } from "@/components/ui/badge";

export default function Home() {
  return (
    <div className="flex h-dvh flex-col">
      <header className="border-b">
        <div className="mx-auto flex max-w-3xl items-center gap-3 px-4 py-3">
          <div className="flex size-7 items-center justify-center rounded-md bg-primary text-xs font-semibold text-primary-foreground">
            A
          </div>
          <h1 className="text-sm font-medium">Acme Cloud Support</h1>
          <Badge variant="outline" className="ml-auto">
            AI agent demo
          </Badge>
        </div>
      </header>
      <ChatPanel />
    </div>
  );
}
