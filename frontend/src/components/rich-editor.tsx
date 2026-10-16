'use client';

import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { Node, type Editor } from '@tiptap/core';
import {
  EditorContent,
  NodeViewWrapper,
  ReactNodeViewRenderer,
  useEditor,
  useEditorState,
  type NodeViewProps,
} from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from '@tiptap/markdown';
import Placeholder from '@tiptap/extension-placeholder';
import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight';
import {
  Bold,
  Italic,
  Strikethrough,
  Code,
  SquareCode,
  List,
  ListOrdered,
  Quote,
  Link,
  FileCode,
  GripVertical,
  X,
  Paperclip,
  AtSign,
} from 'lucide-react';
import { emptyDocument, plainText, safeLink, type RichDocument } from '@/lib/rich-text';
import { api, errorMessage } from '@/lib/api';
import { fileSize, type QueuedFile } from './files';
import { codeLanguages, lowlight } from './rich-message';

const FilesContext = createContext<{
  files: QueuedFile[];
  path: string;
  locked: boolean;
  preserveFiles: boolean;
  retry: (key: string) => void;
  remove: (key: string) => void;
}>({ files: [], path: '', locked: false, preserveFiles: false, retry: () => {}, remove: () => {} });

function AttachmentView({ node, deleteNode }: NodeViewProps) {
  const { files, path, locked, preserveFiles, retry, remove } = useContext(FilesContext);
  const file = files.find((f) => f.key === node.attrs.id);
  return (
    <NodeViewWrapper
      className="editor-attachment"
      contentEditable={false}
      data-attachment-key={node.attrs.id}
    >
      <div className="editor-attachment-card">
        <span className="attachment-drag" data-drag-handle title="Drag to move attachment">
          <GripVertical size={16} />
        </span>
        {file?.attachment &&
        ['image/png', 'image/jpeg', 'image/gif', 'image/webp'].includes(
          file.attachment.content_type,
        ) ? (
          <img
            className="editor-attachment-preview"
            src={`/api/v1${path}/${file.attachment.id}/content?preview=true`}
            alt={file.filename}
          />
        ) : (
          <Paperclip size={20} />
        )}
        <div className="editor-attachment-info">
          <strong>{file?.filename || 'Attachment unavailable'}</strong>
          <small>
            {file
              ? `${fileSize(file.size)} · ${file.attachment ? 'Ready' : file.error || `Uploading ${file.progress || 0}%`}`
              : 'Remove this item and select the file again.'}
          </small>
          {file && !file.attachment && !file.error && (
            <progress
              max={100}
              value={file.progress || 0}
              aria-label={`Uploading ${file.filename}`}
            />
          )}
        </div>
        {file?.error && (
          <button type="button" disabled={locked} onClick={() => retry(file.key)}>
            Retry upload
          </button>
        )}
        {!preserveFiles && (
          <button
            type="button"
            className="icon-button"
            disabled={locked}
            aria-label={`Remove ${file?.filename || 'attachment'}`}
            onClick={() => {
              deleteNode();
              if (file) remove(file.key);
            }}
          >
            <X size={16} />
          </button>
        )}
      </div>
    </NodeViewWrapper>
  );
}

const AttachmentNode = Node.create({
  name: 'attachment',
  group: 'block',
  atom: true,
  draggable: true,
  addAttributes() {
    return { id: { default: null } };
  },
  // Attachments can only enter through the upload flow, never pasted HTML.
  parseHTML() {
    return [];
  },
  renderHTML({ node }) {
    return ['div', { 'data-attachment-id': node.attrs.id }, 'Attachment'];
  },
  addNodeView() {
    return ReactNodeViewRenderer(AttachmentView);
  },
});

const MentionNode = Node.create({
  name: 'mention',
  group: 'inline',
  inline: true,
  atom: true,
  addAttributes() {
    return { id: { default: null }, label: { default: '' } };
  },
  parseHTML() {
    return [];
  },
  renderHTML({ node }) {
    return ['span', { class: 'mention', 'data-user-id': node.attrs.id }, `@${node.attrs.label}`];
  },
  renderText({ node }) {
    return `@${node.attrs.label}`;
  },
});
type Candidate = { user_id: string; label: string; email: string };
type MentionQuery = { from: number; to: number; query: string };

export function RichEditor({
  document,
  change,
  label,
  placeholder,
  locked = false,
  files = [],
  path = '',
  retry = () => {},
  remove = () => {},
  preserveFiles = false,
  register,
  send,
  mentionPath,
}: {
  document?: RichDocument;
  change: (document: RichDocument, body: string) => void;
  label: string;
  placeholder: string;
  locked?: boolean;
  files?: QueuedFile[];
  path?: string;
  retry?: (key: string) => void;
  remove?: (key: string) => void;
  preserveFiles?: boolean;
  register?: (editor: Editor | null) => void;
  send?: () => void;
  mentionPath?: string;
}) {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [mentionError, setMentionError] = useState('');
  const [mentionAttempt, setMentionAttempt] = useState(0);
  const [mention, setMention] = useState<MentionQuery | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const dismissed = useRef('');
  const mentionKeys = useRef<(event: KeyboardEvent) => boolean>(() => false);
  const refreshMention = useRef<(editor: Editor) => void>(() => {});
  useEffect(() => {
    let active = true;
    setCandidates([]);
    setMentionError('');
    if (mentionPath)
      api<Candidate[]>(`${mentionPath}/mention-candidates`)
        .then((rows) => {
          if (active) setCandidates(rows);
        })
        .catch((failure) => {
          if (active) setMentionError(errorMessage(failure));
        });
    return () => {
      active = false;
    };
  }, [mentionPath, mentionAttempt]);
  const callbacks = useRef({ change, send, register, locked });
  callbacks.current = { change, send, register, locked };
  const [panel, setPanel] = useState<'link' | 'markdown' | null>(null);
  const [source, setSource] = useState('');
  const [panelError, setPanelError] = useState('');
  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({
        heading: false,
        horizontalRule: false,
        underline: false,
        codeBlock: false,
        link: { openOnClick: false, protocols: ['http', 'https', 'mailto'] },
      }),
      CodeBlockLowlight.configure({ lowlight, defaultLanguage: 'plaintext' }),
      Placeholder.configure({ placeholder }),
      Markdown,
      AttachmentNode,
      MentionNode,
    ],
    content: document || emptyDocument(),
    editable: !locked,
    editorProps: {
      attributes: {
        role: 'textbox',
        'aria-label': label,
        'aria-multiline': 'true',
        class: 'rich-text rich-editor-input',
      },
      handleKeyDown: (view, event) => {
        if (!callbacks.current.locked && mentionKeys.current(event)) return true;
        if (
          event.key !== 'Enter' ||
          event.isComposing ||
          view.composing ||
          callbacks.current.locked ||
          !callbacks.current.send
        )
          return false;
        if (event.ctrlKey || event.metaKey) {
          event.preventDefault();
          callbacks.current.send();
          return true;
        }
        if (event.shiftKey) return false;
        const from = view.state.selection.$from;
        const fence =
          from.parent.type.name === 'paragraph' &&
          /^```([a-zA-Z0-9_+-]*)$/.exec(from.parent.textContent);
        if (fence) {
          event.preventDefault();
          const start = from.start();
          editor
            ?.chain()
            .deleteRange({ from: start, to: from.end() })
            .setCodeBlock({ language: fence[1] || 'plaintext' })
            .run();
          return true;
        }
        const structured = Array.from(
          { length: from.depth },
          (_, index) => from.node(index + 1).type.name,
        ).some((name) => ['codeBlock', 'listItem', 'blockquote'].includes(name));
        if (structured) return false;
        event.preventDefault();
        callbacks.current.send();
        return true;
      },
      handlePaste: (view, event) => {
        if (callbacks.current.locked || view.state.selection.$from.parent.type.name === 'codeBlock')
          return false;
        const text = event.clipboardData?.getData('text/plain') || '';
        if (event.clipboardData?.files.length || event.clipboardData?.getData('text/html'))
          return false;
        if (/```|\*\*[^*]+\*\*|^\s*[-*] |^\s*\d+\. |\[[^\]]+\]\(https?:/m.test(text)) {
          editor?.commands.insertContent(text, { contentType: 'markdown' });
          return true;
        }
        return false;
      },
    },
    onUpdate: ({ editor }) => {
      callbacks.current.change(editor.getJSON(), plainText(editor.getJSON()).trim());
      refreshMention.current(editor);
    },
    onSelectionUpdate: ({ editor }) => refreshMention.current(editor),
    onCreate: ({ editor }) => callbacks.current.register?.(editor),
    onDestroy: () => callbacks.current.register?.(null),
  });
  refreshMention.current = (current) => {
    const { $from, empty } = current.state.selection;
    if (
      !mentionPath ||
      locked ||
      !empty ||
      $from.parent.type.name !== 'paragraph' ||
      current.isActive('code') ||
      current.isActive('link')
    ) {
      setMention(null);
      return;
    }
    const before = $from.parent.textBetween(0, $from.parentOffset, '\n', '\ufffc');
    const match = /(?:^|\s)@([^\s@]*)$/.exec(before);
    if (!match) {
      setMention(null);
      dismissed.current = '';
      return;
    }
    const from = $from.pos - match[1].length - 1;
    if (dismissed.current === `${from}:${$from.pos}:${match[1]}`) return;
    setMention((previous) =>
      previous?.from === from && previous.to === $from.pos && previous.query === match[1]
        ? previous
        : { from, to: $from.pos, query: match[1] },
    );
    setMentionIndex(0);
  };
  const matches = candidates
    .filter((person) =>
      `${person.label} ${person.email}`.toLowerCase().includes(mention?.query.toLowerCase() || ''),
    )
    .slice(0, 8);
  function chooseMention(person: Candidate) {
    if (!editor || !mention || locked) return;
    editor
      .chain()
      .focus()
      .insertContentAt({ from: mention.from, to: mention.to }, [
        { type: 'mention', attrs: { id: person.user_id, label: person.label } },
        { type: 'text', text: ' ' },
      ])
      .run();
    setMention(null);
  }
  mentionKeys.current = (event) => {
    if (!mention || event.isComposing) return false;
    if (event.key === 'Escape') {
      dismissed.current = `${mention.from}:${mention.to}:${mention.query}`;
      setMention(null);
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setMentionIndex(
        (index) => (index + step + Math.max(matches.length, 1)) % Math.max(matches.length, 1),
      );
    } else if (event.key === 'Enter' || event.key === 'Tab') {
      if (matches.length) chooseMention(matches[mentionIndex % matches.length]);
      else if (event.key === 'Tab') {
        setMention(null);
        return false;
      }
    } else return false;
    event.preventDefault();
    return true;
  };
  const state = useEditorState({
    editor,
    selector: ({ editor }) => ({
      bold: editor?.isActive('bold'),
      italic: editor?.isActive('italic'),
      strike: editor?.isActive('strike'),
      code: editor?.isActive('code'),
      codeBlock: editor?.isActive('codeBlock'),
      bulletList: editor?.isActive('bulletList'),
      orderedList: editor?.isActive('orderedList'),
      blockquote: editor?.isActive('blockquote'),
      link: editor?.isActive('link'),
      language: editor?.getAttributes('codeBlock').language || 'plaintext',
    }),
  });
  useEffect(() => {
    editor?.setEditable(!locked, false);
  }, [editor, locked]);
  useEffect(() => {
    let active = true;
    if (editor && document && JSON.stringify(document) !== JSON.stringify(editor.getJSON()))
      queueMicrotask(() => {
        if (active && !editor.isDestroyed)
          editor.commands.setContent(document, { emitUpdate: false });
      });
    return () => {
      active = false;
    };
  }, [editor, document]);
  useEffect(() => {
    register?.(editor);
  }, [editor, register]);
  const tools = [
    {
      label: 'Bold',
      icon: Bold,
      active: state?.bold,
      run: () => editor?.chain().focus().toggleBold().run(),
    },
    {
      label: 'Italic',
      icon: Italic,
      active: state?.italic,
      run: () => editor?.chain().focus().toggleItalic().run(),
    },
    {
      label: 'Strikethrough',
      icon: Strikethrough,
      active: state?.strike,
      run: () => editor?.chain().focus().toggleStrike().run(),
    },
    {
      label: 'Inline code',
      icon: Code,
      active: state?.code,
      run: () => editor?.chain().focus().toggleCode().run(),
    },
    {
      label: 'Code block',
      icon: SquareCode,
      active: state?.codeBlock,
      run: () => editor?.chain().focus().toggleCodeBlock().run(),
    },
    {
      label: 'Bullet list',
      icon: List,
      active: state?.bulletList,
      run: () => editor?.chain().focus().toggleBulletList().run(),
    },
    {
      label: 'Numbered list',
      icon: ListOrdered,
      active: state?.orderedList,
      run: () => editor?.chain().focus().toggleOrderedList().run(),
    },
    {
      label: 'Block quote',
      icon: Quote,
      active: state?.blockquote,
      run: () => editor?.chain().focus().toggleBlockquote().run(),
    },
  ];
  return (
    <FilesContext.Provider value={{ files, path, locked, preserveFiles, retry, remove }}>
      <div className="rich-editor">
        <div className="formatting-toolbar" role="toolbar" aria-label="Message formatting">
          {tools.map(({ label, icon: Icon, active, run }) => (
            <button
              key={label}
              type="button"
              className="icon-button"
              aria-label={label}
              title={label}
              aria-pressed={!!active}
              disabled={locked || !editor}
              onClick={run}
            >
              <Icon size={16} />
            </button>
          ))}
          <button
            type="button"
            className="icon-button"
            aria-label="Add link"
            title="Add link"
            aria-pressed={!!state?.link}
            disabled={locked || !editor}
            onClick={() => {
              setPanel('link');
              setSource(editor?.getAttributes('link').href || '');
              setPanelError('');
            }}
          >
            <Link size={16} />
          </button>
          <button
            type="button"
            className="icon-button"
            aria-label="Insert Markdown"
            title="Insert Markdown"
            disabled={locked || !editor}
            onClick={() => {
              setPanel('markdown');
              setSource('');
              setPanelError('');
            }}
          >
            <FileCode size={16} />
          </button>
          {mentionPath && (
            <button
              type="button"
              className="icon-button"
              aria-label="Mention someone"
              title="Mention someone"
              disabled={locked || !editor || !!state?.codeBlock}
              onClick={() => {
                dismissed.current = '';
                editor?.chain().focus().insertContent(' @').run();
              }}
            >
              <AtSign size={16} />
            </button>
          )}
          {state?.codeBlock && (
            <select
              aria-label="Code language"
              disabled={locked}
              value={state.language}
              onChange={(e) =>
                editor
                  ?.chain()
                  .focus()
                  .updateAttributes('codeBlock', { language: e.target.value })
                  .run()
              }
            >
              {!codeLanguages.includes(state.language) && (
                <option value={state.language}>{state.language}</option>
              )}
              {codeLanguages.map((language) => (
                <option key={language}>{language}</option>
              ))}
            </select>
          )}
        </div>
        {panel && (
          <div className="editor-insert-panel">
            <label>
              {panel === 'link' ? 'Link URL' : 'Markdown'}
              {panel === 'link' ? (
                <input
                  aria-label="Link URL"
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                  placeholder="https://"
                  disabled={locked}
                />
              ) : (
                <textarea
                  aria-label="Markdown"
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                  rows={4}
                  maxLength={12000}
                  disabled={locked}
                />
              )}
            </label>
            {panelError && <small role="alert">{panelError}</small>}
            <div className="button-row">
              <button
                type="button"
                disabled={locked}
                onClick={() => {
                  if (panel === 'link') {
                    if (!source) editor?.chain().focus().extendMarkRange('link').unsetLink().run();
                    else if (!safeLink(source)) {
                      setPanelError('Use an http, https, or mailto link.');
                      return;
                    } else if (editor?.state.selection.empty)
                      editor
                        ?.chain()
                        .focus()
                        .insertContent({
                          type: 'text',
                          text: source,
                          marks: [{ type: 'link', attrs: { href: source } }],
                        })
                        .run();
                    else editor?.chain().focus().setLink({ href: source }).run();
                  } else
                    editor
                      ?.chain()
                      .focus()
                      .insertContent(source, { contentType: 'markdown' })
                      .run();
                  setPanel(null);
                }}
              >
                {panel === 'link' ? 'Apply link' : 'Insert'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setPanel(null);
                  editor?.commands.focus();
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
        {mention && !locked && (
          <div className="mention-picker" aria-label="Mention a conversation member">
            <div role="listbox" aria-label="Mention suggestions">
              {matches.map((person, index) => (
                <button
                  type="button"
                  role="option"
                  aria-selected={index === mentionIndex % Math.max(matches.length, 1)}
                  key={person.user_id}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => chooseMention(person)}
                >
                  <strong>@{person.label}</strong>
                  <small>{person.email}</small>
                </button>
              ))}
            </div>
            {!matches.length && <p>{mentionError || 'No matching conversation members.'}</p>}
            {mentionError && (
              <button type="button" onClick={() => setMentionAttempt((value) => value + 1)}>
                Retry loading members
              </button>
            )}
            <small>Arrow keys to choose · Enter to mention · Esc to close</small>
          </div>
        )}
        <EditorContent editor={editor} />
      </div>
    </FilesContext.Provider>
  );
}
