import { UNRESOLVED_MODEL_SENTINEL } from "@/chat-core";

import { ChatViewClient } from "../chat-view-client";

export function generateStaticParams() {
  return [{ botId: "local", channel: "general" }];
}

interface ChatChannelPageProps {
  params: Promise<{ botId: string; channel: string }>;
}

export default async function ChatChannelPage({ params }: ChatChannelPageProps) {
  const { botId, channel } = await params;

  return (
    <ChatViewClient
      botId={botId}
      botName="Local Agent"
      botStatus="active"
      modelSelection={UNRESOLVED_MODEL_SENTINEL}
      bots={[]}
      maxBots={1}
      initialChannel={channel}
      telegramBotUsername={null}
      telegramOwnerId={null}
    />
  );
}
