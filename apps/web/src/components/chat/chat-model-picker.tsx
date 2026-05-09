interface ChatModelPickerProps {
  botId: string;
  modelSelection: string;
  routerType?: string | null;
  apiKeyMode: string;
  subscriptionPlan?: string | null;
  persistMode?: "bot" | "local";
  menuPlacement?: "bottom" | "top";
  onModelSelectionChange?: (modelSelection: string, routerType: string) => void;
}

export function ChatModelPicker(_props: ChatModelPickerProps) {
  return (
    <div
      className="flex h-11 max-w-[13rem] items-center rounded-lg border border-black/[0.06] bg-white/80 px-3 text-xs font-medium text-foreground/80 shadow-[0_1px_8px_rgba(15,23,42,0.06)] backdrop-blur sm:h-8 sm:px-2.5"
      data-chat-model-picker="true"
      title="Uses the model configured in magi-agent.yaml"
    >
      <span className="truncate">Configured LLM</span>
    </div>
  );
}
