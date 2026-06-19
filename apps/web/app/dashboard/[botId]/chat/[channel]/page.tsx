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
      modelSelection="claude-sonnet-4-20250514"
      bots={[]}
      maxBots={1}
      initialChannel={channel}
      telegramBotUsername={null}
      telegramOwnerId={null}
    />
  );
}
