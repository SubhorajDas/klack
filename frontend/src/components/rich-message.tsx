'use client';

import { useState, type ReactNode } from 'react';
import { common, createLowlight } from 'lowlight';
import type { Element, RootContent } from 'hast';
import { attachmentKeys, safeLink, type RichDocument } from '@/lib/rich-text';
import { channelPath, type Message } from '@/lib/types';
import { AttachmentCards, MessageAttachments } from './files';

export const lowlight = createLowlight(common);
export const codeLanguages = [
  'plaintext',
  'javascript',
  'typescript',
  'python',
  'json',
  'html',
  'css',
  'bash',
  'sql',
  'go',
  'rust',
  'java',
  'yaml',
];

function highlighted(node: RootContent, key: number): ReactNode {
  if (node.type === 'text') return node.value;
  if (node.type !== 'element') return null;
  const element = node as Element;
  return (
    <span
      key={key}
      className={
        Array.isArray(element.properties.className)
          ? element.properties.className.join(' ')
          : undefined
      }
    >
      {element.children.map(highlighted)}
    </span>
  );
}

function CodeSnippet({ node }: { node: RichDocument }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const text = (node.content || []).map((child) => child.text || '').join('');
  const language = String(node.attrs?.language || 'plaintext');
  const content = lowlight.registered(language)
    ? lowlight.highlight(language, text).children.map(highlighted)
    : text;
  return (
    <div className="code-snippet">
      <div className="code-snippet-bar">
        <span>{language}</span>
        <button
          type="button"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(text);
              setCopied(true);
              setFailed(false);
            } catch {
              setFailed(true);
            }
          }}
        >
          {copied ? 'Copied' : failed ? 'Copy failed — retry' : 'Copy code'}
        </button>
      </div>
      <pre>
        <code>{content}</code>
      </pre>
    </div>
  );
}

export function RichMessage({ message }: { message: Message }) {
  if (message.deleted_at) return <p>This message was deleted.</p>;
  if (!message.document)
    return (
      <>
        <p>{message.body}</p>
        <MessageAttachments message={message} />
      </>
    );
  const path = `${channelPath(message.workspace_id, message.channel_id)}/files`;
  function render(node: RichDocument, key: number): ReactNode {
    const children = node.content?.map(render);
    switch (node.type) {
      case 'text': {
        let text: ReactNode = node.text;
        for (const mark of node.marks || []) {
          if (mark.type === 'bold') text = <strong>{text}</strong>;
          if (mark.type === 'italic') text = <em>{text}</em>;
          if (mark.type === 'strike') text = <s>{text}</s>;
          if (mark.type === 'code') text = <code>{text}</code>;
          if (mark.type === 'link') {
            const href = safeLink(mark.attrs?.href);
            if (href)
              text = (
                <a href={href} target="_blank" rel="noopener noreferrer">
                  {text}
                </a>
              );
          }
        }
        return <span key={key}>{text}</span>;
      }
      case 'paragraph':
        return <p key={key}>{children || <br />}</p>;
      case 'hardBreak':
        return <br key={key} />;
      case 'blockquote':
        return <blockquote key={key}>{children}</blockquote>;
      case 'bulletList':
        return <ul key={key}>{children}</ul>;
      case 'orderedList':
        return (
          <ol key={key} start={node.attrs?.start || 1}>
            {children}
          </ol>
        );
      case 'listItem':
        return <li key={key}>{children}</li>;
      case 'codeBlock':
        return <CodeSnippet key={key} node={node} />;
      case 'attachment': {
        const file = message.attachments?.find((f) => f.id === node.attrs?.id);
        return file ? (
          <AttachmentCards key={key} files={[file]} path={path} />
        ) : (
          <p key={key}>Attachment unavailable.</p>
        );
      }
      default:
        return <div key={key}>{children}</div>;
    }
  }
  // Preserve files on older or incomplete document payloads too.
  const placed = new Set(attachmentKeys(message.document));
  const unplaced = (message.attachments || []).filter((f) => !placed.has(f.id));
  return (
    <div className="rich-text message-rich-text">
      {message.document.content?.map(render)}
      {unplaced.length > 0 && <AttachmentCards files={unplaced} path={path} />}
    </div>
  );
}
