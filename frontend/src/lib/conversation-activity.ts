import type { Message } from './types';

export type ReadPosition = { user_id: string; message_id: string; created_at: string };

export function readersFor(message: Message, positions: ReadPosition[], self: string) {
  return positions.filter((position) => {
    if (position.user_id === self) return false;
    const time = Date.parse(position.created_at) - Date.parse(message.created_at);
    if (time !== 0) return time > 0;
    // PostgreSQL preserves microseconds; Date only preserves milliseconds.
    const fraction = (value: string) =>
      (value.match(/\.(\d+)/)?.[1] || '').padEnd(9, '0').slice(3, 9);
    const cursorFraction = fraction(position.created_at);
    const messageFraction = fraction(message.created_at);
    return (
      cursorFraction > messageFraction ||
      (cursorFraction === messageFraction && position.message_id >= message.id)
    );
  });
}

export function typingLabel(names: string[]) {
  if (!names.length) return '';
  if (names.length === 1) return `${names[0]} is typing…`;
  const count =
    ['Zero', 'One', 'Two', 'Three', 'Four', 'Five'][names.length] || String(names.length);
  return `${count} people are typing…`;
}
