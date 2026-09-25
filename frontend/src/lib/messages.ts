import type { Message } from './types';

/** Highest revision wins, regardless of REST/socket arrival order. */
export function mergeMessages(current: Message[], incoming: Message[]) {
  const messages = new Map(current.map((message) => [message.id, message]));
  for (const message of incoming) {
    const previous = messages.get(message.id);
    if (!previous || message.revision > previous.revision) messages.set(message.id, message);
  }
  return [...messages.values()].sort(
    (a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
  );
}
