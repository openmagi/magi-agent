type ChannelLike = {
  name: string;
};

export function getFirstChannelName<T extends ChannelLike>(channels: readonly T[]): string | null {
  return channels[0]?.name ?? null;
}

export function getNextChannelAfterDeletion<T extends ChannelLike>(
  channels: readonly T[],
  deletedChannelName: string,
): string | null {
  return getFirstChannelName(channels.filter((channel) => channel.name !== deletedChannelName));
}
