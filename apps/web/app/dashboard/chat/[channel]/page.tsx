"use client";

import { use } from "react";
import { ChatViewClient } from "../chat-view-client";

interface ChatChannelPageProps {
  params: Promise<{ channel: string }>;
}

export default function ChatChannelPage({ params }: ChatChannelPageProps) {
  const { channel } = use(params);

  return (
    <ChatViewClient
      botId="local"
      botName="Local Agent"
      botStatus="active"
      modelSelection="claude-sonnet-4-20250514"
      apiKeyMode="byok"
      routerType={null}
      subscriptionPlan="pro"
      bots={[]}
      maxBots={1}
      initialChannel={channel}
      telegramBotUsername={null}
      telegramOwnerId={null}
    />
  );
}
