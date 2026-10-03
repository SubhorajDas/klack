import type { Message, MessageQuote } from './types';

export function quoteMessage(message: Message): MessageQuote {
  return {
    id: message.id,
    author_user_id: message.author_user_id,
    body: message.deleted_at ? null : (message.body || '').slice(0, 240),
    deleted_at: message.deleted_at,
    revision: message.revision,
    attachment_count: message.deleted_at ? 0 : message.attachments?.length || 0,
  };
}

/** Highest revision wins, regardless of REST/socket arrival order. */
export function mergeMessages(current: Message[], incoming: Message[]) {
  const quotes = new Map<string, MessageQuote>();
  for (const message of [...current, ...incoming]) {
    for (const quote of [message.quote, quoteMessage(message)]) {
      if (quote && (!quotes.has(quote.id) || quotes.get(quote.id)!.revision < quote.revision))
        quotes.set(quote.id, quote);
    }
  }
  const messages = new Map(current.map((message) => [message.id, message]));
  for (const message of incoming) {
    const previous = messages.get(message.id);
    if (!previous || message.revision > previous.revision) messages.set(message.id, message);
  }
  return [...messages.values()]
    .map((message) => ({
      ...message,
      ...(message.reply_to_message_id
        ? {
            quote: message.deleted_at
              ? null
              : quotes.get(message.reply_to_message_id) || message.quote,
          }
        : {}),
    }))
    .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
}
