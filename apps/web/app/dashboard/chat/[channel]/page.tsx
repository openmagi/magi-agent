import { UNRESOLVED_MODEL_SENTINEL } from "@/chat-core";

import { ChatViewClient } from "../chat-view-client";

export function generateStaticParams() {
  return [{ channel: "general" }];
}

interface ChatChannelPageProps {
  params: Promise<{ channel: string }>;
}

export default async function ChatChannelPage({ params }: ChatChannelPageProps) {
  const { channel } = await params;

  return (
    <ChatViewClient
      botId="local"
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
