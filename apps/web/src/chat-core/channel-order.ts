import type { Channel } from "./types";

function byPositionThenName(a: Channel, b: Channel): number {
  if (a.position !== b.position) return a.position - b.position;
  return a.name.localeCompare(b.name);
}

export function reconcileChannelsWithLocalOrder(
  serverChannels: Channel[],
  localChannels: Channel[],
): Channel[] {
  if (localChannels.length === 0) return serverChannels;

  const serverByName = new Map(serverChannels.map((channel) => [channel.name, channel]));
  const used = new Set<string>();

  const ordered = localChannels.flatMap((localChannel) => {
    const serverChannel = serverByName.get(localChannel.name);
    if (!serverChannel) return [];
    used.add(localChannel.name);
    return [{
      ...serverChannel,
      category: localChannel.category ?? serverChannel.category,
      position: localChannel.position,
    }];
  });

  const additions = serverChannels
    .filter((channel) => !used.has(channel.name))
    .sort(byPositionThenName);

  return [...ordered, ...additions].map((channel, position) => ({
    ...channel,
    position,
  }));
}
