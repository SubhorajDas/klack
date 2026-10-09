import type { JSONContent } from '@tiptap/core';
import type { Message } from './types';

export type RichDocument = JSONContent;
export const emptyDocument = (): RichDocument => ({
  type: 'doc',
  content: [{ type: 'paragraph' }],
});

export function plainText(node: RichDocument): string {
  if (node.type === 'text') return node.text || '';
  if (node.type === 'hardBreak') return '\n';
  return (node.content || [])
    .map(plainText)
    .join(node.type === 'paragraph' || node.type === 'codeBlock' ? '' : '\n');
}

export function attachmentKeys(node: RichDocument): string[] {
  return node.type === 'attachment'
    ? [String(node.attrs?.id || '')]
    : (node.content || []).flatMap(attachmentKeys);
}

export function replaceAttachmentKeys(node: RichDocument, ids: Map<string, string>): RichDocument {
  return {
    ...node,
    ...(node.type === 'attachment'
      ? { attrs: { id: ids.get(node.attrs?.id) || node.attrs?.id } }
      : {}),
    ...(node.content
      ? { content: node.content.map((child) => replaceAttachmentKeys(child, ids)) }
      : {}),
  };
}

export function legacyDocument(body: string, ids: string[] = []): RichDocument {
  return {
    type: 'doc',
    content: [
      ...body.split('\n').map((text) => ({
        type: 'paragraph',
        ...(text ? { content: [{ type: 'text', text }] } : {}),
      })),
      ...ids.map((id) => ({ type: 'attachment', attrs: { id } })),
      ...(ids.length ? [{ type: 'paragraph' }] : []),
    ],
  };
}

export function messageDocument(message: Message): RichDocument {
  return (
    message.document ||
    legacyDocument(
      message.body || '',
      (message.attachments || []).map((f) => f.id),
    )
  );
}

export function safeLink(href: unknown): string | undefined {
  return typeof href === 'string' &&
    /^(https?:\/\/|mailto:)/i.test(href) &&
    !/[\u0000-\u0020]/.test(href)
    ? href
    : undefined;
}
