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
      modelSelection="claude-sonnet-4-20250514"
      apiKeyMode="byok"
      routerType={null}
      subscriptionPlan={null}
      bots={[]}
      maxBots={1}
      initialChannel={channel}
      telegramBotUsername={null}
      telegramOwnerId={null}
    />
  );
}
